"""Postgres-backed durable ingestion job queue (Phase 12).

Public API:
    from app.jobs import enqueue, get_job, list_jobs
    job_id = enqueue(org_id, connection_id)
    job = get_job(org_id, job_id)
"""

from .queue import (
    IngestionJob,
    JobAlreadyActiveError,
    enqueue,
    has_active_job,
    claim_next,
    mark_succeeded,
    mark_failed,
    reap_stuck,
    get_job,
    list_jobs,
    get_connection_provider,
)
from .worker import run_once, run_forever

__all__ = [
    "IngestionJob",
    "JobAlreadyActiveError",
    "enqueue",
    "has_active_job",
    "claim_next",
    "mark_succeeded",
    "mark_failed",
    "reap_stuck",
    "get_job",
    "list_jobs",
    "get_connection_provider",
    "run_once",
    "run_forever",
]
