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

from ..auth import get_connection_config, get_live_connection_token
from ..auth.credentials import (
    clear_needs_reauth,
    looks_like_auth_failure,
    mark_needs_reauth,
)
from ..config.settings import ContextualSettings
from ..core.exceptions import OAuthReauthRequiredError
from ..ingestion.pipeline import enrich_source_contextual, ingest_source
from ..sources import build_source_adapter
from . import queue

logger = logging.getLogger(__name__)


def run_once() -> queue.IngestionJob | None:
    """Claim and process a single queued job, if any.

    Returns the job's final record (``succeeded`` or ``failed``), or ``None``
    if the queue was empty. Any exception during ingestion is caught and
    recorded on the job as ``failed`` rather than propagating — a worker loop
    must survive one bad job and keep polling.
    """
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


def run_forever(*, poll_interval: float = 5.0, reap_interval: int = 60) -> None:
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
    while True:
        now = time.monotonic()
        if now - last_reap >= reap_interval:
            queue.reap_stuck()
            last_reap = now

        job = run_once()
        if job is None:
            time.sleep(poll_interval)
