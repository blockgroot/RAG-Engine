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
from ..config.settings import ContextualSettings
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
        queue.mark_failed(job.id, str(exc))

    return queue.get_job(job.org_id, job.id)


def run_forever(*, poll_interval: float = 5.0, reap_interval: int = 60) -> None:
    """Poll for queued jobs forever, reaping stuck ``running`` jobs periodically."""
    import time

    last_reap = 0.0
    while True:
        now = time.monotonic()
        if now - last_reap >= reap_interval:
            queue.reap_stuck()
            last_reap = now

        job = run_once()
        if job is None:
            time.sleep(poll_interval)
