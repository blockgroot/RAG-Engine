"""Ingestion job worker (Phase 12).

Claims one queued job at a time and runs ``ingest_source`` (incremental by
default) against it — durability and progress tracking only; the pipeline owns
how pages are fetched and upserted.
"""

from __future__ import annotations

from ..auth import get_connection_config, get_live_connection_token
from ..ingestion.pipeline import ingest_source
from ..sources import build_source_adapter
from . import queue


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
        token = get_live_connection_token(job.org_id, provider)
        config = get_connection_config(job.org_id, provider)
        adapter = build_source_adapter(provider, token=token, config=config)
        result = ingest_source(adapter, job.org_id, provider=provider, incremental=True)
        # doc_count = pages written this run (added + updated), not remote total.
        queue.mark_succeeded(job.id, result.documents_ingested)
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
