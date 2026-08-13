"""Ingestion job worker (Phase 12).

Claims one queued job at a time and runs ``ingest_source`` (incremental by
default) against it — durability and progress tracking only; the pipeline owns
how pages are fetched and upserted.

When contextual retrieval is enabled with ``defer=True`` (the default), the
fast sync finishes and the job is marked ``succeeded`` first so onboarding can
unlock; contextualize + re-embed then runs as a best-effort ``enriching``
phase so Phase 6 quality still lands without blocking UX.
"""

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
from ..config.settings import ContextualSettings, IngestWorkerSettings
from ..core.exceptions import OAuthReauthRequiredError
from ..ingestion.pipeline import enrich_source_contextual, ingest_source
from ..sources import build_source_adapter
from . import queue

logger = logging.getLogger(__name__)


def _current_rss_mb() -> float:
    """CURRENT process RSS in MB, for the memory admission gate.

    Deliberately NOT ``resource.getrusage(...).ru_maxrss``: that is a
    monotonic **high-water mark** which never decreases even after memory is
    freed (verified: a process that allocates 300MB and frees it still reports
    312MB). Using it here was an outright bug — once the process had *ever*
    peaked above the ceiling, the gate below would refuse to claim work for the
    rest of the process's life, silently disabling ingestion entirely rather
    than throttling it.

    ``/proc/self/statm`` field 2 is resident pages, the real current figure on
    Linux (Render's runtime). ``ps`` is the portable fallback for local dev on
    macOS, where there is no ``/proc``; it costs a subprocess, which is
    acceptable at one call per poll tick. If neither works we return 0.0 —
    failing *open* on purpose, because a broken measurement must not be able to
    block all ingestion.
    """
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
    """Claim and process a single queued job, if any.

    Returns the job's final record (``succeeded`` or ``failed``), ``None`` if
    the queue was empty, or ``None`` if the memory admission gate declined to
    claim anything this tick (see ``_memory_guard_ok`` — the job, if any,
    stays queued for the next tick or another worker instance). Any exception
    during ingestion is caught and recorded on the job as ``failed`` rather
    than propagating — a worker loop must survive one bad job and keep
    polling.
    """
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
            """Persist live progress so a poller sees the run advance.

            Without this the job row is unchanged from ``running`` until the
            very end, so a multi-minute sync looks identical to a hung one.
            """
            queue.update_progress(
                job.id, phase=phase, processed=processed, total=total
            )

        result = ingest_source(
            adapter,
            job.org_id,
            provider=provider,
            incremental=True,
            workspace_id=job.workspace_id,
            on_progress=report,
        )
        # Unlock the product as soon as raw chunks are stored.
        queue.mark_succeeded(job.id, result.documents_ingested)
        clear_needs_reauth(job.org_id, provider, job.workspace_id)

        contextual = ContextualSettings.from_env()
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
    """Delete rows nothing will ever read again. Never raises.

    Two tables only ever grew: ``api_rate_counters`` (one row per scope per
    closed window) and ``query_answer_cache`` (expired rows are invisible to
    ``get`` but were never removed). Neither is load-bearing, so this is
    swallowed on failure exactly like progress reporting — housekeeping must
    never be able to stop ingestion.

    Lives on the worker's existing periodic tick rather than an external cron so
    the single self-hosted image keeps needing no scheduler (§1).
    """
    try:
        from ..rag.query_cache import prune_expired

        removed = prune_expired()
        if removed:
            logger.info("Pruned %s expired query-cache row(s)", removed)
    except Exception:  # noqa: BLE001 - housekeeping must never break the worker
        logger.debug("Query-cache prune failed", exc_info=True)

    try:
        from ..security.rate_limit import prune_old_windows

        removed = prune_old_windows()
        if removed:
            logger.info("Pruned %s closed rate-limit window(s)", removed)
    except Exception:  # noqa: BLE001
        logger.debug("Rate-counter prune failed", exc_info=True)


def run_forever(
    *,
    poll_interval: float = 5.0,
    reap_interval: int = 60,
    maintenance_interval: int = 3600,
) -> None:
    """Poll for queued jobs forever, reaping stuck ``running`` jobs periodically."""
    import time

    try:
        n = queue.requeue_interrupted_running()
        if n:
            logger.info(
                "Re-queued %s interrupted ingestion job(s) after worker start", n
            )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to re-queue interrupted ingestion jobs")

    last_reap = 0.0
    # Start "already due" like last_reap so one sweep runs shortly after boot —
    # on a free instance that restarts often, waiting a full hour every time
    # would mean the prune effectively never happens.
    last_maintenance = 0.0
    while True:
        now = time.monotonic()
        if now - last_reap >= reap_interval:
            queue.reap_stuck()
            last_reap = now
        if now - last_maintenance >= maintenance_interval:
            run_maintenance()
            last_maintenance = now

        job = run_once()
        if job is None:
            time.sleep(poll_interval)
