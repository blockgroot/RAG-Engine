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
from ..sources import build_source_adapter
from . import queue

logger = logging.getLogger(__name__)


def _clear_answer_cache(org_id: str, job_id: str) -> None:
    """Drop this org's cached answers. Best-effort, never fails the job.

    Called on every path that changes what a question would retrieve — the
    ingest itself, and deferred contextual enrichment, which rewrites chunk
    content after the ingest already reported success.
    """
    try:
        from ..rag.query_cache import delete_org_entries

        dropped = delete_org_entries(org_id)
        if dropped:
            logger.info("Job %s: cleared %s cached answer(s)", job_id, dropped)
    except Exception as exc:  # noqa: BLE001 - a cache miss beats a failed ingest
        logger.warning("Job %s: could not clear the answer cache: %s", job_id, exc)


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
            # Every sync, not only when an admin presses Check: a rename is
            # invisible to change detection, so without this the stored label
            # can stay wrong indefinitely and every thread title, suggestion
            # chip and report coverage note keeps showing the old name.
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
        queue.mark_succeeded(job.id, result.documents_ingested)
        clear_needs_reauth(job.org_id, provider, job.workspace_id)

        # New content must not sit behind a cached answer: without this the
        # sync is invisible for up to QUERY_CACHE_TTL_SECONDS (300s), and to
        # the person who just pressed Update that reads as "the sync did
        # nothing".
        if result.documents_ingested or result.documents_removed:
            _clear_answer_cache(job.org_id, job.id)

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
                # Enrichment REWRITES chunk content, so it moves the answer
                # again — and it runs after the clear above, which would
                # otherwise have been repopulated with pre-enrichment answers
                # by any question asked in between.
                _clear_answer_cache(job.org_id, job.id)
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
    try:
        from ..rag.query_cache import prune_expired

        removed = prune_expired()
        if removed:
            logger.info("Pruned %s expired query-cache row(s)", removed)
    except Exception:  # noqa: BLE001 - housekeeping must never break the worker
        logger.debug("Query-cache prune failed", exc_info=True)

    # Slack channel labels. Every OTHER trigger for this needs someone to do
    # something — press Check, run a sync, own a scheduler that comes due — so
    # a renamed channel could stay unfindable indefinitely on a quiet org. This
    # is the one path that runs on its own.
    #
    # Cheap enough for the hourly tick: one paginated conversations.list per
    # connected Slack workspace, and it only writes when a name actually moved.
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

    try:
        from ..security.rate_limit import prune_old_windows

        removed = prune_old_windows()
        if removed:
            logger.info("Pruned %s closed rate-limit window(s)", removed)
    except Exception:  # noqa: BLE001
        logger.debug("Rate-counter prune failed", exc_info=True)


def run_sync_tick() -> int:
    """Enqueue any due connection syncs. Never raises. Returns how many.

    Split from ``run_maintenance`` (hourly) because a webhook-flagged sync must
    not wait up to an hour to be picked up — the whole point of push is that it
    is faster than the poll floor.
    """
    try:
        from .autosync import enqueue_due_syncs

        return enqueue_due_syncs()
    except Exception:  # noqa: BLE001 - must never break the shared worker loop
        logger.exception("Auto-sync tick failed")
        return 0


def run_external_tick() -> dict[str, int]:
    """One externally-driven pass: reap, sync, run due schedulers.

    Exists because a free-tier Render web service spins down after ~15 minutes
    with no INBOUND request — an in-process timer does not keep it alive, so
    every background loop silently stops and freshness stops with it. A free
    external cron (cron-job.org and friends) hitting ``POST /internal/tick``
    both wakes the process and drives this, which is why waking and working are
    the same request rather than a ping plus a hope.

    Deliberately excludes ``run_maintenance``: pruning expired cache rows is
    hourly housekeeping, and doing it on every external tick would spend writes
    on nothing. Returns per-step counts so the caller (and the cron's own log)
    can see the tick did something.
    """
    from ..config.settings import SchedulerSettings

    reaped = 0
    try:
        reaped = queue.reap_stuck()
    except Exception:  # noqa: BLE001
        logger.exception("External tick: reap failed")

    synced = run_sync_tick()

    scheduler_settings = SchedulerSettings.from_env()
    schedulers_ran = (
        run_scheduler_tick(scheduler_settings) if scheduler_settings.enabled else 0
    )

    return {"reaped": reaped, "syncs_queued": synced, "schedulers_ran": schedulers_ran}


def run_scheduler_tick(settings=None) -> int:
    """Run any due activity schedulers. Never raises. Returns how many ran.

    Wrapped here rather than at each call site because both worker paths
    (this module's ``run_forever`` and the in-API loop in ``app/api/main.py``)
    need identical swallowing: a scheduler batch that raised would abort the
    ingestion tick sharing the same loop, coupling two unrelated features.
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
) -> None:
    """Poll for queued jobs forever, reaping stuck ``running`` jobs periodically.

    Also ticks the activity scheduler, on the same interleaved-timer pattern
    as reaping and maintenance, so the standalone worker process and the
    in-API worker behave identically (see ``app/api/main.py``). Running
    schedulers in only one of the two would mean reports silently stop
    whenever ``INGEST_WORKER_IN_API`` is flipped.
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
    last_scheduler = -float(scheduler_settings.poll_seconds)
    while True:
        now = time.monotonic()
        if now - last_reap >= reap_interval:
            queue.reap_stuck()
            last_reap = now
        if now - last_maintenance >= maintenance_interval:
            run_maintenance()
            last_maintenance = now
        if now - last_sync >= sync_interval:
            run_sync_tick()
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
