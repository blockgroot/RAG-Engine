"""Background connection syncing — freshness with nobody pressing a button.

Before this, nothing in the codebase ever ingested unless a human opened
Sources and pressed Check → Update. That made staleness a *user chore*: the
answer to "is this answer current?" was "only if someone remembered", and the
Activity Scheduler inherited the same gap for any source read from the index
rather than live.

Two reasons to sync, and exactly two columns for them
-----------------------------------------------------
``oauth_connections.sync_requested_at``
    A service TOLD us something changed. Stamped by a webhook handler
    (``app/api/webhooks.py``), never by this module.

``oauth_connections.last_sync_at``
    Nothing told us anything, but ``interval_hours`` has passed and we should
    look anyway.

The poll is the FLOOR, not the plan. It exists because push is not universally
available on a free deployment:

* Slack / Linear / Notion push an event, and Drive pushes through a
  ``changes.watch`` channel we must renew (``sources/drive_watch.py``), so a
  change syncs within one tick. (Drive used to be poll-only because Google
  required a verified push domain; Google has since dropped that requirement.)
* Linear's app webhook covers PUBLIC teams only, and every channel can lapse,
  so private-team issues and missed pushes still rely on the interval.
* A webhook delivered while the free-tier box was cold-started is simply
  lost. The interval is what makes that a delay instead of a permanent hole.

Why the flag is a flag and not a queue
--------------------------------------
A busy Slack channel stamps ``sync_requested_at`` once per message. Fifty
messages produce ONE job, because the tick reads the column and clears it —
the coalescing is the data model, not a debounce timer. This is also why the
webhook handler must never ingest inline: Slack requires a 3-second ack, and
an ingest is minutes.
"""

from __future__ import annotations

import logging

from ..config.settings import AutoSyncSettings
from ..db.connection import get_connection
from . import queue

logger = logging.getLogger(__name__)


def request_sync(org_id: str, provider: str, workspace_id: str | None = None) -> int:
    """Mark a connection as having pending changes. Returns rows stamped.

    Called by webhook handlers. Deliberately does NOT enqueue: stamping is a
    single indexed UPDATE that answers inside a provider's ack deadline,
    whereas ``queue.enqueue`` competes with an already-active job and would
    make a webhook's success depend on scheduling state.

    ``workspace_id=None`` here means "org-wide connection only", matching
    every other scope-paired read. A webhook usually cannot tell which of an
    org's scopes it belongs to, so callers that only know the external
    workspace should resolve the connection first and pass its scope.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "UPDATE oauth_connections SET sync_requested_at = now() "
            "WHERE org_id = %s AND provider = %s "
            "AND workspace_id IS NOT DISTINCT FROM %s "
            "RETURNING 1",
            (org_id, provider, workspace_id),
        ).fetchall()
    return len(rows)


#: A push starts its sync at once only if the connection has not synced in this
#: many minutes; otherwise the flag waits for the next tick. A busy Slack
#: channel pushes once per message, and Slack allows our app about one history
#: read a minute, so syncing on EVERY push would run syncs back to back into
#: that limit. Inside the gap the change still lands, one tick later.
PUSH_SYNC_COOLDOWN_MINUTES = 3

_FLAG_RETURNING = (
    " RETURNING id::text, org_id::text, workspace_id::text, provider,"
    " (last_sync_at IS NULL OR last_sync_at < now() - make_interval(mins => %s))"
)


def _flag_and_start(sql: str, params: list) -> int:
    """Stamp the flag, then queue a sync right away where the cooldown allows.

    The flag used to be ALL a push did, so a change waited for the next tick
    (~10 min) even though the in-API worker runs a queued job within seconds
    and the push itself has just woken the instance. Queuing here is
    ``sync_now``: a no-op while a sync is already active (a burst still makes
    one job), and on success it clears the flag. When it does not queue -- an
    active job, the cooldown, a failure -- the flag stays for the tick, which
    is what makes this an addition and never the only path. Callers run it
    AFTER the provider has been answered, so the ack deadline is unaffected.
    """
    with get_connection() as conn:
        rows = conn.execute(sql + _FLAG_RETURNING, [*params, PUSH_SYNC_COOLDOWN_MINUTES]).fetchall()
    for connection_id, org_id, workspace_id, provider, cooled_down in rows:
        if cooled_down:
            sync_now(org_id, connection_id, provider=provider, workspace_id=workspace_id)
    return len(rows)


def request_sync_connection(connection_id: str) -> int:
    """Flag ONE connection, for a push that already names it (a Drive channel)."""
    return _flag_and_start(
        "UPDATE oauth_connections SET sync_requested_at = now() "
        "WHERE id = %s::uuid AND needs_reauth = false",
        [connection_id],
    )


def request_sync_external(
    provider: str, external_workspace_id: str, *, slack_channel: str | None = None
) -> int:
    """Flag every connection a webhook is about, and start their syncs. Returns rows.

    A webhook names the PROVIDER'S workspace (a Slack team, a Notion
    workspace, a Linear organization), never our org or space -- and one
    external workspace can back several connections (org-wide plus a space).
    All of them are flagged: a spurious sync is one cheap listing diff, a
    missed one is an hour of staleness. Each then starts at once unless it
    synced within ``PUSH_SYNC_COOLDOWN_MINUTES`` (see ``_flag_and_start``).

    ``slack_channel`` narrows a Slack event to connections that actually
    index that channel, so a message in #random does not sync a space that
    only connected #engineering. ``needs_reauth`` rows are left alone: the
    tick skips them anyway, and a flag nobody will clear is noise.
    """
    if not external_workspace_id:
        return 0
    sql = (
        "UPDATE oauth_connections SET sync_requested_at = now() "
        "WHERE provider = %s AND external_workspace_id = %s AND needs_reauth = false"
    )
    params: list = [provider, external_workspace_id]
    if slack_channel is not None:
        sql += " AND coalesce(source_config -> 'channel_ids', '[]'::jsonb) ? %s"
        params.append(slack_channel)
    return _flag_and_start(sql, params)


#: Providers with an ``oauth_connections`` row but no ingestion path at all.
#: GitHub deliberately embeds nothing (``app/githublive/``) — it has no
#: ``SourceAdapter``, so queueing one costs a guaranteed "Unknown source type"
#: failure on every single tick. Excluded here rather than at the worker
#: because a job that can only ever fail should never reach the queue.
UNSYNCABLE_PROVIDERS = ("github",)

#: Providers that sync by recording COUNTABLE FACTS rather than by ingesting.
#:
#: GitHub only. It embeds nothing, so there is nothing to chunk -- but its pull
#: requests are countable, and charts need them from a background sync rather
#: than from a page load (see ``app/insights/github_facts.py``). This is a
#: SEPARATE path on purpose: it never touches ``ingestion_jobs``, so the
#: "Unknown source type: 'github'" failure that ``UNSYNCABLE_PROVIDERS``
#: prevents stays impossible. Both constants name github, and both are right.
FACTS_ONLY_PROVIDERS = ("github",)


def _due_connections(settings: AutoSyncSettings) -> list[tuple[str, str, str, str]]:
    """Connections that should be synced now: ``(id, org_id, workspace_id, why)``.

    Providers in ``UNSYNCABLE_PROVIDERS`` are skipped because they have no
    ingestion path; ``needs_reauth`` rows are skipped because a dead token
    cannot be fixed by retrying it, and hammering one is how an org gets
    rate-limited for a problem only a reconnect solves.

    Ordered oldest-first so a starved connection cannot be permanently
    overtaken by a chattier one when ``batch_size`` truncates the list.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id::text,
                   org_id::text,
                   workspace_id::text,
                   CASE WHEN sync_requested_at IS NOT NULL
                        THEN 'webhook' ELSE 'interval' END
            FROM oauth_connections
            WHERE needs_reauth = false
              AND provider <> ALL(%s)
              AND (
                    sync_requested_at IS NOT NULL
                 OR last_sync_at IS NULL
                 OR last_sync_at < now() - make_interval(hours => %s)
              )
            ORDER BY coalesce(sync_requested_at, last_sync_at) NULLS FIRST
            LIMIT %s
            """,
            (list(UNSYNCABLE_PROVIDERS), settings.interval_hours, settings.batch_size),
        ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def _due_facts_connections(
    settings: AutoSyncSettings,
) -> list[tuple[str, str, str, str]]:
    """Facts-only connections due now: ``(id, org_id, workspace_id, why)``.

    The same freshness predicate as ``_due_connections`` -- webhook flag, or
    the interval elapsed -- against the opposite provider set. ``needs_reauth``
    rows are skipped for the same reason: a dead token cannot be fixed by
    retrying it.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id::text,
                   org_id::text,
                   workspace_id::text,
                   CASE WHEN sync_requested_at IS NOT NULL
                        THEN 'webhook' ELSE 'interval' END
            FROM oauth_connections
            WHERE needs_reauth = false
              AND provider = ANY(%s)
              AND (
                    sync_requested_at IS NOT NULL
                 OR last_sync_at IS NULL
                 OR last_sync_at < now() - make_interval(hours => %s)
              )
            ORDER BY coalesce(sync_requested_at, last_sync_at) NULLS FIRST
            LIMIT %s
            """,
            (list(FACTS_ONLY_PROVIDERS), settings.interval_hours, settings.batch_size),
        ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def record_due_facts(settings: AutoSyncSettings | None = None) -> int:
    """Record facts for every due facts-only connection. Returns how many ran.

    Runs INLINE rather than through the queue, which is the whole point: there
    is no ingestion job to enqueue, and inventing one would need a source
    adapter that deliberately does not exist. Bounded by ``batch_size`` and by
    the reader's own per-repo caps.

    Never raises, like ``enqueue_due_syncs`` -- this shares the worker tick
    with the ingestion queue and the activity scheduler.
    """
    settings = settings or AutoSyncSettings.from_env()
    if not settings.enabled:
        return 0

    try:
        due = _due_facts_connections(settings)
    except Exception:  # noqa: BLE001 - housekeeping must never break the worker
        logger.exception("Auto-sync: could not list due facts connections")
        return 0

    from ..insights.github_facts import record_github_facts

    ran = 0
    for connection_id, org_id, workspace_id, why in due:
        try:
            result = record_github_facts(org_id, workspace_id=workspace_id)
            ran += 1
            try:
                from ..graph.builder import build_facts

                build_facts(org_id, workspace_id)
            except Exception:  # noqa: BLE001 - a stale graph, never lost facts
                logger.warning("Auto-sync: graph build failed for %s", connection_id, exc_info=True)
            logger.info(
                "Auto-sync: recorded %s GitHub facts for org %s (%s)",
                result.written, org_id, why,
            )
        except Exception:  # noqa: BLE001 - the recorder is already broad
            logger.exception("Auto-sync: could not record facts for %s", connection_id)
        # Stamped even on failure, exactly like an ingest attempt: one failed
        # sync costs one interval of freshness, which is visible, while a hot
        # retry loop against a provider's rate limit is not.
        try:
            _stamp_attempted(connection_id)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-sync: could not stamp %s", connection_id)

    return ran


def _stamp_attempted(connection_id: str) -> None:
    """Record that we tried, and clear the webhook flag.

    Stamped on ATTEMPT, not on ingest success — deliberately. A connection
    whose ingest keeps failing must not be retried every single tick forever;
    the interval throttles it, and ``needs_reauth`` catches the auth case. The
    cost of the choice is that one failed sync delays freshness by one
    interval, which is visible, whereas a hot retry loop against a provider's
    rate limit is not.

    Clearing ``sync_requested_at`` in the same statement is what collapses a
    burst: anything stamped after this read simply lands in the next tick.
    """
    with get_connection() as conn:
        conn.execute(
            "UPDATE oauth_connections "
            "SET last_sync_at = now(), sync_requested_at = NULL WHERE id = %s",
            (connection_id,),
        )


#: Providers whose ingestion needs a SCOPE the admin picks after connecting,
#: and the ``source_config`` key that holds it. Their adapters raise without one
#: (``GoogleDriveAdapter`` on an empty ``folder_id``, ``SlackAdapter`` on empty
#: ``channel_ids``), so a FIRST connect is not yet the moment to ingest --
#: saving the scope is, and that is where ``sync_now`` is called from instead.
#:
#: One dict rather than a set plus a hand-kept copy of the keys: `api/
#: notifications.py` needed exactly this mapping to report "connected but
#: indexing nothing", and two lists of the same fact drift.
SCOPE_KEYS: dict[str, str] = {"google": "folder_id", "slack": "channel_ids"}
SCOPED_PROVIDERS = tuple(SCOPE_KEYS)


def scope_is_configured(
    org_id: str, provider: str, workspace_id: str | None = None
) -> bool:
    """Does this connection already know WHAT to read?

    True for any provider that needs no scope at all. Never raises: an
    unreadable config means we simply do not claim the scope is there.
    """
    key = SCOPE_KEYS.get(provider)
    if key is None:
        return True
    try:
        from ..auth.credentials import get_connection_config

        return bool((get_connection_config(org_id, provider, workspace_id) or {}).get(key))
    except Exception:  # noqa: BLE001 - never fail a connect over a lookup
        logger.warning("Could not read %s scope config for org %s", provider, org_id)
        return False


def sync_after_connect(
    org_id: str,
    connection_id: str,
    *,
    provider: str,
    workspace_id: str | None = None,
) -> str | None:
    """Queue the ingest an OAuth callback should trigger, if any.

    RECONNECTING is not the same event as connecting, and treating them alike
    is what this exists to fix. ``save_connection`` upserts the tokens and
    deliberately leaves ``source_config`` alone, so a Drive folder or a Slack
    channel list SURVIVES a reconnect -- which means the reason
    ``SCOPED_PROVIDERS`` skip the callback ("the adapter raises without a
    scope") is true on a first connect and false on every one after it.

    The cost of the old blanket skip was the case people actually hit: a token
    expires, the connector stops syncing, someone reconnects to fix exactly
    that, and nothing happens for up to an interval -- on the one screen where
    they are watching for it to. Linear reconnected and immediately indexed
    because it is not scoped; Drive sat silent, and the difference read as Drive
    being broken.
    """
    if provider in SCOPE_KEYS and not scope_is_configured(org_id, provider, workspace_id):
        logger.info(
            "Connect: %s has no scope saved yet; the scope-save route will queue it",
            provider,
        )
        return None
    return sync_now(org_id, connection_id, provider=provider, workspace_id=workspace_id)


def sync_now(
    org_id: str,
    connection_id: str,
    *,
    provider: str,
    workspace_id: str | None = None,
) -> str | None:
    """Ingest this connection immediately. Returns the job id, or None.

    Connecting a source, or naming the folder or channels it should read, IS
    the request to index it — waiting for the next tick makes a working
    connection look broken for up to an interval, and the whole point of
    automatic freshness was to delete that chore rather than move it.

    Stamps ``last_sync_at`` exactly as the tick does, so this connection does
    not immediately re-qualify as due and get a second job behind this one.

    Never raises: an already-active job is a no-op (the work is happening), and
    a failure to queue must not fail the connect or the scope save that just
    succeeded — the tick still picks it up, which is the behaviour this
    replaces rather than depends on.
    """
    if provider in UNSYNCABLE_PROVIDERS:
        return None
    try:
        job_id = queue.enqueue(org_id, connection_id, workspace_id=workspace_id)
    except queue.JobAlreadyActiveError:
        logger.debug("First sync: %s already has an active job", connection_id)
        return None
    except Exception:  # noqa: BLE001
        logger.exception("First sync: could not queue %s", connection_id)
        return None
    try:
        _stamp_attempted(connection_id)
    except Exception:  # noqa: BLE001
        logger.exception("First sync: could not stamp %s", connection_id)
    logger.info("First sync: queued %s (org %s)", connection_id, org_id)
    return job_id


def enqueue_due_syncs(settings: AutoSyncSettings | None = None) -> int:
    """Enqueue an ingest for every due connection. Returns how many.

    Never raises: this runs on a shared worker tick alongside the ingestion
    queue and the activity scheduler, and a broken sync must not take those
    down with it.

    An already-active job for the same connection is a NO-OP, not an error —
    ``queue.enqueue``'s unique partial index refuses the duplicate, which is
    the correct outcome: the work is already happening.
    """
    settings = settings or AutoSyncSettings.from_env()
    if not settings.enabled:
        return 0

    try:
        due = _due_connections(settings)
    except Exception:  # noqa: BLE001 - housekeeping must never break the worker
        logger.exception("Auto-sync: could not list due connections")
        return 0

    enqueued = 0
    for connection_id, org_id, workspace_id, why in due:
        try:
            queue.enqueue(org_id, connection_id, workspace_id=workspace_id)
            enqueued += 1
            logger.info(
                "Auto-sync: queued %s (%s, org %s)", connection_id, why, org_id
            )
        except queue.JobAlreadyActiveError:
            # Already syncing. Still stamp, so a long-running job does not make
            # this connection re-qualify on every tick for its whole duration.
            logger.debug("Auto-sync: %s already has an active job", connection_id)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-sync: could not queue %s", connection_id)
            continue
        try:
            _stamp_attempted(connection_id)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-sync: could not stamp %s", connection_id)

    return enqueued
