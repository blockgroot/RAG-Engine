"""Background worker for ingestion jobs."""

from __future__ import annotations

import logging
import os
import subprocess

from ..auth import get_connection_config, get_live_connection_token
from ..auth.credentials import (
    clear_needs_reauth,
    looks_like_auth_failure,
    mark_needs_reauth,
)
from ..config.settings import ContextualSettings, IngestWorkerSettings, env_bool
from ..core.exceptions import OAuthReauthRequiredError
from ..ingestion.pipeline import enrich_source_contextual, ingest_source
from ..insights.facts import DOCUMENT_PROVIDERS, backfill_all_document_facts, record_document_facts
from ..sources import build_source_adapter
from . import queue
from .autosync import start_cooled_down_pushes

#: How often a worker loop starts pushes whose cooldown has ended (`autosync`).
PUSH_CHECK_SECONDS = 15.0

logger = logging.getLogger(__name__)


def _build_graph(org_id: str, provider: str, workspace_id: str | None, result) -> None:
    """Fold what this job wrote into the knowledge graph. Best-effort.

    Built from stored rows (no provider call); removed documents cascade.
    Never fails a finished job, which would make it a retry loop.
    """
    external_ids = list(getattr(result, "ingested_external_ids", None) or []) + list(
        getattr(result, "meta_refreshed_external_ids", None) or []
    )
    try:
        from ..graph.builder import build_documents, collect_garbage

        if external_ids:
            build_documents(org_id, workspace_id, provider, external_ids)
        elif getattr(result, "documents_removed", 0):
            collect_garbage(org_id, workspace_id)
        if provider == "slack":
            # Membership moves without messages changing: rebuild every sync.
            from ..graph.builder import build_memberships

            build_memberships(org_id, workspace_id)
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning(
            "graph: could not build %s for org %s", provider, org_id, exc_info=True
        )


def _record_insight_facts(
    org_id: str, provider: str, workspace_id: str | None, adapter=None
) -> None:
    """Turn what we just indexed into countable facts. Best-effort.

    Runs on every successful job, so it backfills itself. ``doc_changed``
    comes from `documents`; Linear's issue facts need the adapter (state and
    assignee are not columns), and on the tick the connection would already
    be stamped not-due. GitHub's come from ``run_facts_tick``. Never fails a
    finished job.
    """
    if provider in DOCUMENT_PROVIDERS:
        try:
            record_document_facts(org_id, provider=provider, workspace_id=workspace_id)
        except Exception:  # noqa: BLE001 - breadth is the point, see docstring
            logger.warning(
                "insights: could not record %s facts for org %s", provider, org_id,
                exc_info=True,
            )

    if provider == "google":
        # Form responses are never indexed: classified to a sentiment label,
        # text discarded.
        try:
            from ..insights.facts import record_form_facts_if_enabled

            record_form_facts_if_enabled(org_id, workspace_id=workspace_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "insights: could not classify form responses for org %s", org_id,
                exc_info=True,
            )

    if provider == "linear" and adapter is not None:
        try:
            from ..insights.linear_facts import record_linear_facts

            record_linear_facts(org_id, workspace_id=workspace_id, adapter=adapter)
        except Exception:  # noqa: BLE001
            logger.warning(
                "insights: could not record Linear issue facts for org %s", org_id,
                exc_info=True,
            )


def _contextual_settings_for(provider: str | None) -> ContextualSettings:
    """Disable contextualization for Slack unless explicitly re-enabled."""
    settings = ContextualSettings.from_env()
    if provider == "slack" and not env_bool("SLACK_CONTEXTUAL_ENABLED", False):
        return ContextualSettings(
            enabled=False,
            defer=settings.defer,
            concurrency=settings.concurrency,
            max_chunks=settings.max_chunks,
        )
    return settings


def _current_rss_mb() -> float:
    """Current process RSS in MB for the memory admission gate."""
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            resident_pages = int(fh.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return int(out.stdout.strip()) / 1024  # ps reports KB
    except Exception:  # noqa: BLE001 - measurement must never break the worker
        logger.debug("Could not read current RSS; memory gate will allow work")
        return 0.0


def _memory_guard_ok(settings: IngestWorkerSettings) -> bool:
    if not settings.memory_guard_enabled:
        return True
    return _current_rss_mb() < settings.max_rss_mb


def run_once() -> queue.IngestionJob | None:
    """Claim and process one queued job, if any."""
    memory_settings = IngestWorkerSettings.from_env()
    if not _memory_guard_ok(memory_settings):
        logger.warning(
            "Skipping ingestion job claim: process RSS (%.0fMB) is at/above "
            "the configured ceiling (%.0fMB) -- deferring to the next poll",
            _current_rss_mb(),
            memory_settings.max_rss_mb,
        )
        return None

    job = queue.claim_next()
    if job is None:
        return None

    provider = None
    try:
        provider = queue.get_connection_provider(job.connection_id, job.org_id)
        token = get_live_connection_token(job.org_id, provider, workspace_id=job.workspace_id)
        config = get_connection_config(job.org_id, provider, workspace_id=job.workspace_id)
        adapter = build_source_adapter(provider, token=token, config=config)

        def report(phase: str, processed: int, total: int) -> None:
            """Persist live progress for polling clients."""
            queue.update_progress(
                job.id, phase=phase, processed=processed, total=total
            )

        if provider == "slack":
            # Every sync: a rename is invisible to change detection.
            from ..sources.slack_utils import refresh_channel_names

            for old, new in refresh_channel_names(job.org_id, job.workspace_id):
                logger.info("Job %s: channel #%s is now #%s", job.id, old, new)

        contextual = _contextual_settings_for(provider)
        result = ingest_source(
            adapter,
            job.org_id,
            provider=provider,
            incremental=True,
            workspace_id=job.workspace_id,
            on_progress=report,
            contextual=contextual,
        )
        queue.mark_succeeded(
            job.id,
            result.documents_ingested,
            result.documents_permission_unreadable,
        )
        clear_needs_reauth(job.org_id, provider, job.workspace_id)

        _record_insight_facts(job.org_id, provider, job.workspace_id, adapter)
        _build_graph(job.org_id, provider, job.workspace_id, result)

        if (
            contextual.enabled
            and contextual.defer
            and result.ingested_external_ids
        ):
            try:
                enrich_source_contextual(
                    adapter,
                    job.org_id,
                    provider=provider,
                    external_ids=result.ingested_external_ids,
                    workspace_id=job.workspace_id,
                    contextual=contextual,
                    on_progress=report,
                )
            except Exception as exc:  # noqa: BLE001 - enrich must not flip success→failed
                logger.warning(
                    "Deferred contextual enrich failed for job %s: %s",
                    job.id,
                    exc,
                )
    except Exception as exc:  # noqa: BLE001 - a job failure must never crash the worker
        if provider and (
            isinstance(exc, OAuthReauthRequiredError) or looks_like_auth_failure(exc)
        ):
            try:
                mark_needs_reauth(
                    job.org_id, provider, job.workspace_id, str(exc)
                )
            except Exception:  # noqa: BLE001 - health flag must not mask the job error
                logger.warning(
                    "Could not mark needs_reauth for job %s", job.id, exc_info=True
                )
        queue.mark_failed(job.id, str(exc))

    return queue.get_job(job.org_id, job.id)


def run_maintenance() -> None:
    """Delete expired housekeeping rows without raising."""
    # Slack channel labels: the one refresh that needs no activity. One
    # conversations.list per workspace; writes only on a rename.
    try:
        from ..sources.slack_utils import refresh_channel_names

        from ..db.connection import get_connection

        with get_connection() as conn:
            slack = conn.execute(
                "SELECT org_id::text, workspace_id::text FROM oauth_connections "
                "WHERE provider = 'slack'"
            ).fetchall()
        for org_id, workspace_id in slack:
            for old, new in refresh_channel_names(org_id, workspace_id):
                logger.info(
                    "Maintenance: Slack channel #%s is now #%s (org %s)",
                    old,
                    new,
                    org_id,
                )
    except Exception:  # noqa: BLE001 - housekeeping must never break the worker
        logger.debug("Slack channel-name refresh failed", exc_info=True)

    # A newer Handbook: email this server's admins once (self-hosted only).
    try:
        from ..updates.announce import announce_new_release

        announce_new_release()
    except Exception:  # noqa: BLE001 - a missed email, never a stopped worker
        logger.exception("Release announcement failed")


def run_sync_tick() -> int:
    """Enqueue any due connection syncs. Never raises. Returns how many.

    Not hourly like maintenance: a webhook-flagged sync must not wait an hour.
    """
    try:
        from .autosync import enqueue_due_syncs

        return enqueue_due_syncs()
    except Exception:  # noqa: BLE001 - must never break the shared worker loop
        logger.exception("Auto-sync tick failed")
        return 0


def run_facts_tick() -> int:
    """Record facts for any due facts-only connection. Never raises.

    Kept apart from ``run_sync_tick`` so GitHub never enters ``ingestion_jobs``.
    """
    try:
        from .autosync import record_due_facts

        return record_due_facts()
    except Exception:  # noqa: BLE001 - must never break the shared worker loop
        logger.exception("Facts tick failed")
        return 0


def run_external_tick() -> dict[str, int]:
    """One externally-driven pass: reap, sync, backfill, purge, run schedulers.

    ``POST /internal/tick`` from an outside cron both wakes a host that sleeps
    without inbound traffic and does the work. ``run_maintenance`` is hourly
    and not run here. Returns per-step counts.
    """
    from ..config.settings import SchedulerSettings

    reaped = 0
    try:
        reaped = queue.reap_stuck()
    except Exception:  # noqa: BLE001
        logger.exception("External tick: reap failed")

    synced = run_sync_tick()
    facts = run_facts_tick()

    # Drive push channels expire within a week; a lapsed one is only polled.
    drive_watches = 0
    try:
        from ..sources.drive_watch import ensure_watches

        drive_watches = ensure_watches()
    except Exception:  # noqa: BLE001 - a lapsed push, never a failed tick
        logger.exception("External tick: Drive watch renewal failed")

    # Document facts for tenants not ingested since charts shipped.
    backfilled = 0
    try:
        backfilled = backfill_all_document_facts()
    except Exception:  # noqa: BLE001 - a missing chart, never a failed tick
        logger.exception("External tick: document-fact backfill failed")

    # Graph backfill, bounded per tick (`graph.builder.BACKFILL_BATCH`).
    try:
        from ..graph.builder import backfill as backfill_graph

        backfill_graph()
    except Exception:  # noqa: BLE001 - a thinner graph, never a failed tick
        logger.exception("External tick: graph backfill failed")

    # Expire chat attachments: the shorter clock for a file in a live chat.
    attachments_purged = 0
    try:
        from ..attachments import purge_expired_attachments

        attachments_purged = purge_expired_attachments()
    except Exception:  # noqa: BLE001
        logger.exception("External tick: attachment purge failed")

    # Retention: removes whole chats (attachments and turns cascade).
    conversations_purged = 0
    try:
        from ..memory.conversations import purge_expired_conversations

        conversations_purged = purge_expired_conversations()
    except Exception:  # noqa: BLE001
        logger.exception("External tick: conversation purge failed")

    # Live-read audit rows past 90 days.
    try:
        from ..livetools.audit import purge_expired as purge_live_calls

        purge_live_calls()
    except Exception:  # noqa: BLE001 - a longer log, never a failed tick
        logger.exception("External tick: live_tool_calls purge failed")

    # Injection scores ingest could not write (rate limit, or older chunks).
    # A no-op when GUARD_MODE=off.
    injection_scored = 0
    try:
        from ..guard.backfill import backfill_injection_scores

        injection_scored = backfill_injection_scores()
    except Exception:  # noqa: BLE001 - an unscored chunk, never a failed tick
        logger.exception("External tick: injection-score backfill failed")

    scheduler_settings = SchedulerSettings.from_env()
    schedulers_ran = (
        run_scheduler_tick(scheduler_settings) if scheduler_settings.enabled else 0
    )

    return {
        "reaped": reaped,
        "syncs_queued": synced,
        "facts_recorded": facts,
        "drive_watches_opened": drive_watches,
        "facts_backfilled": backfilled,
        "attachments_purged": attachments_purged,
        "conversations_purged": conversations_purged,
        "injection_scored": injection_scored,
        "schedulers_ran": schedulers_ran,
    }


def run_scheduler_tick(settings=None) -> int:
    """Run any due activity schedulers. Never raises: a scheduler failure
    must not abort the ingestion loop it shares. Returns how many ran.
    """
    try:
        from ..schedulers.worker import run_due_schedulers_once

        ran = run_due_schedulers_once(settings)
        if ran:
            logger.info("Ran %s due scheduler(s)", ran)
        return ran
    except Exception:  # noqa: BLE001 - must never break the shared worker loop
        logger.exception("Scheduler tick failed")
        return 0


def run_forever(
    *,
    poll_interval: float = 5.0,
    reap_interval: int = 60,
    maintenance_interval: int = 3600,
    sync_interval: int = 300,
    tick_interval: int = 600,
) -> None:
    """Poll for queued jobs forever, with interleaved timers for reaping,
    pushes, maintenance, syncs and schedulers (same as the in-API worker).

    Also runs ``run_external_tick``: a self-hosted install has no cron
    calling ``/internal/tick``. Each step is safe to repeat.
    """
    import time

    from ..config.settings import SchedulerSettings

    scheduler_settings = SchedulerSettings.from_env()

    try:
        n = queue.requeue_interrupted_running()
        if n:
            logger.info(
                "Re-queued %s interrupted ingestion job(s) after worker start", n
            )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to re-queue interrupted ingestion jobs")

    try:
        from ..jobs.scheduler_queue import requeue_interrupted_running as requeue_sched

        n = requeue_sched(max_attempts=scheduler_settings.max_attempts)
        if n:
            logger.info("Re-queued %s interrupted scheduler run(s)", n)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to re-queue interrupted scheduler runs")

    last_reap = 0.0
    last_maintenance = 0.0
    last_sync = 0.0
    last_push = 0.0
    last_tick = 0.0
    last_scheduler = -float(scheduler_settings.poll_seconds)
    while True:
        now = time.monotonic()
        if now - last_tick >= tick_interval:
            run_external_tick()
            last_tick = now
        if now - last_reap >= reap_interval:
            queue.reap_stuck()
            last_reap = now
        if now - last_push >= PUSH_CHECK_SECONDS:
            start_cooled_down_pushes()
            last_push = now
        if now - last_maintenance >= maintenance_interval:
            run_maintenance()
            last_maintenance = now
        if now - last_sync >= sync_interval:
            run_sync_tick()
            # Same cadence: one freshness story per connector.
            run_facts_tick()
            last_sync = now
        if (
            scheduler_settings.enabled
            and now - last_scheduler >= scheduler_settings.poll_seconds
        ):
            run_scheduler_tick(scheduler_settings)
            last_scheduler = now

        job = run_once()
        if job is None:
            time.sleep(poll_interval)
