"""Background connection syncing.

Two reasons to sync, two columns:
``sync_requested_at`` (a webhook said something changed) and ``last_sync_at``
(``interval_hours`` passed). The poll is the floor: Linear's webhook covers
public teams only, Drive watch channels lapse, and a push to a cold box is lost.

``sync_requested_at`` is a flag, not a queue: fifty messages make one job,
because the tick reads and clears it. Webhooks never ingest inline (Slack's
3-second ack).
"""

from __future__ import annotations

import logging

from ..config.settings import AutoSyncSettings
from ..db.connection import get_connection
from . import queue

logger = logging.getLogger(__name__)


def request_sync(org_id: str, provider: str, workspace_id: str | None = None) -> int:
    """Mark a connection as having pending changes. Returns rows stamped.

    Does not enqueue: one UPDATE fits a provider's ack deadline.
    ``workspace_id=None`` means the org-wide connection only.
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


#: A push syncs at once only if the connection has not synced in this many
#: minutes; Slack allows about one history read a minute, and a busy channel
#: pushes per message. Inside the gap the flag waits.
PUSH_SYNC_COOLDOWN_MINUTES = 3

_FLAG_RETURNING = (
    " RETURNING id::text, org_id::text, workspace_id::text, provider,"
    " (last_sync_at IS NULL OR last_sync_at < now() - make_interval(mins => %s))"
)


def _flag_and_start(sql: str, params: list) -> int:
    """Stamp the flag, then ``sync_now`` where the cooldown allows.

    When it does not queue, the flag stays for the tick. Callers run this
    after answering the provider, so the ack deadline is unaffected.
    """
    with get_connection() as conn:
        rows = conn.execute(sql + _FLAG_RETURNING, [*params, PUSH_SYNC_COOLDOWN_MINUTES]).fetchall()
    for connection_id, org_id, workspace_id, provider, cooled_down in rows:
        if cooled_down:
            sync_now(org_id, connection_id, provider=provider, workspace_id=workspace_id)
    return len(rows)


def start_cooled_down_pushes() -> int:
    """Start every flagged connection whose cooldown has just ended.

    Called every few seconds, so a burst's later messages sync once the
    cooldown ends, not at the next tick. Skips connections with an active job.
    Never raises.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT c.id::text, c.org_id::text, c.workspace_id::text, c.provider
                FROM oauth_connections c
                WHERE c.sync_requested_at IS NOT NULL
                  AND c.needs_reauth = false
                  AND c.provider <> ALL(%s)
                  AND (c.last_sync_at IS NULL
                       OR c.last_sync_at < now() - make_interval(mins => %s))
                  AND NOT EXISTS (SELECT 1 FROM ingestion_jobs j WHERE j.connection_id = c.id
                                  AND j.status IN ('queued', 'running'))
                """,
                (list(UNSYNCABLE_PROVIDERS), PUSH_SYNC_COOLDOWN_MINUTES),
            ).fetchall()
    except Exception:  # noqa: BLE001 - the tick still clears every flag
        logger.exception("Push sync: could not list flagged connections")
        return 0
    started = 0
    for connection_id, org_id, workspace_id, provider in rows:
        if sync_now(org_id, connection_id, provider=provider, workspace_id=workspace_id):
            started += 1
    return started


def request_sync_connection(connection_id: str) -> int:
    """Flag every Drive folder on the Google account a channel belongs to.

    A notification names the account, not the folder, and Google skipped
    some channels in production; a folder with nothing new is a cheap diff.
    """
    return _flag_and_start(
        """
        UPDATE oauth_connections SET sync_requested_at = now()
         WHERE (org_id, provider, external_workspace_id) = (
                   SELECT org_id, provider, external_workspace_id
                     FROM oauth_connections WHERE id = %s::uuid)
           AND needs_reauth = false
           AND (id = %s::uuid OR source_config ->> 'folder_id' IS NOT NULL)
        """,
        [connection_id, connection_id],
    )


def request_sync_external(
    provider: str, external_workspace_id: str, *, slack_channel: str | None = None
) -> int:
    """Flag every connection a webhook is about, and start their syncs. Returns rows.

    A webhook names the provider's workspace, which may back several
    connections; all are flagged (a spurious sync is a cheap diff).
    ``slack_channel`` keeps it to connections indexing that channel.
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


#: Connections with no ingestion path (GitHub embeds nothing); a job for one
#: could only fail.
UNSYNCABLE_PROVIDERS = ("github",)

#: Synced by recording chart facts (``insights/github_facts.py``), never
#: through ``ingestion_jobs``.
FACTS_ONLY_PROVIDERS = ("github",)


def _due_connections(settings: AutoSyncSettings) -> list[tuple[str, str, str, str]]:
    """Connections that should be synced now: ``(id, org_id, workspace_id, why)``.

    Skips ``needs_reauth`` (retrying a dead token only gets rate-limited).
    Oldest first, so ``batch_size`` never starves a connection.
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
    """Facts-only connections due now, by ``_due_connections``'s predicate."""
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

    Inline, not queued (there is no source adapter). Bounded by
    ``batch_size`` and per-repo caps. Never raises: it shares the worker tick.
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
        # Stamped on attempt: no hot retry loop against a rate limit.
        try:
            _stamp_attempted(connection_id)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-sync: could not stamp %s", connection_id)

    return ran


def _stamp_attempted(connection_id: str) -> None:
    """Record that we tried, and clear the webhook flag.

    On attempt, not success: a failing connection waits an interval instead
    of retrying every tick. Clearing the flag here collapses a burst.
    """
    with get_connection() as conn:
        conn.execute(
            "UPDATE oauth_connections "
            "SET last_sync_at = now(), sync_requested_at = NULL WHERE id = %s",
            (connection_id,),
        )


#: Providers whose adapters need a picked scope, and its ``source_config`` key.
#: A first connect does not ingest; saving the scope calls ``sync_now``.
SCOPE_KEYS: dict[str, str] = {"google": "folder_id", "slack": "channel_ids", "linear": "team_ids"}
SCOPED_PROVIDERS = tuple(SCOPE_KEYS)


def scope_is_configured(
    org_id: str, provider: str, workspace_id: str | None = None
) -> bool:
    """Does this connection know what to read? True when no scope is needed;
    False on an unreadable config. Never raises.
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

    A reconnect keeps ``source_config``, so a scoped provider whose scope
    survived syncs at once; only a first connect waits for the picker.
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

    Stamps ``last_sync_at`` like the tick, so it is not due again at once.
    Never raises: an active job is a no-op, and a failure must not fail the
    connect or scope save (the tick still picks it up).
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

    Never raises (shared worker tick). An already-active job is a no-op.
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
            # Still stamp, or a long job re-qualifies every tick.
            logger.debug("Auto-sync: %s already has an active job", connection_id)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-sync: could not queue %s", connection_id)
            continue
        try:
            _stamp_attempted(connection_id)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-sync: could not stamp %s", connection_id)

    return enqueued
