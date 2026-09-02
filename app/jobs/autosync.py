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

* Slack / Linear / Notion push an event, so they sync within one tick.
* **Drive can only be polled** — Google requires the push-notification
  receiver's domain to be *verified in Google Cloud Console*, which a
  ``*.onrender.com`` host can never be.
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
