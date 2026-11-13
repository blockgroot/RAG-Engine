"""Postgres-backed durable ingestion job queue.

``FOR UPDATE SKIP LOCKED`` gives an at-least-once queue that survives a
restart with a truthful status, with no new infrastructure. Reads are scoped
by the session's ``org_id``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime

from psycopg.errors import UniqueViolation

from ..core.exceptions import ConfigurationError
from ..db.connection import DatabaseError, get_connection

logger = logging.getLogger(__name__)

DEFAULT_REAP_TIMEOUT_MINUTES = 30

# Claims before `requeue_interrupted_running` gives up: room for a deploy or
# reload, but a job that kills the process every time is bounded.
DEFAULT_MAX_JOB_ATTEMPTS = int(os.getenv("INGEST_MAX_JOB_ATTEMPTS") or 3)


@dataclass(frozen=True)
class IngestionJob:
    """A row of ``ingestion_jobs``."""

    id: str
    org_id: str
    connection_id: str
    status: str  # queued | running | succeeded | failed
    doc_count: int | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    workspace_id: str | None = None
    # Live progress while running; ``doc_count`` is the final figure.
    phase: str | None = None
    total_documents: int | None = None
    processed_documents: int = 0
    # Claims so far; bounds `requeue_interrupted_running`.
    attempts: int = 0
    # Files whose sharing could not be read: left out (new) or kept their
    # viewers (indexed). Shown on the Sources card.
    permission_unreadable_documents: int = 0


def _row_to_job(row) -> IngestionJob:
    return IngestionJob(
        id=row[0],
        org_id=row[1],
        connection_id=row[2],
        status=row[3],
        doc_count=row[4],
        error=row[5],
        started_at=row[6],
        finished_at=row[7],
        created_at=row[8],
        workspace_id=row[9],
        phase=row[10],
        total_documents=row[11],
        processed_documents=row[12] or 0,
        attempts=row[13] or 0,
        permission_unreadable_documents=row[14] or 0,
    )


_SELECT_COLUMNS = (
    "id::text, org_id::text, connection_id::text, status, doc_count, error, "
    "started_at, finished_at, created_at, workspace_id::text, "
    "phase, total_documents, processed_documents, attempts, "
    "permission_unreadable_documents"
)


class JobAlreadyActiveError(Exception):
    """Raised when enqueue would create a second queued/running job for a connection."""


def has_active_job(org_id: str, connection_id: str) -> bool:
    """True if this connection already has a queued or running job."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM ingestion_jobs "
            "WHERE org_id = %s AND connection_id = %s "
            "AND status IN ('queued', 'running') "
            "LIMIT 1",
            (org_id, connection_id),
        ).fetchone()
    return row is not None


def enqueue(org_id: str, connection_id: str, workspace_id: str | None = None) -> str:
    """Enqueue an ingestion job for this connection. Returns the job id.

    ``workspace_id``: None is org-wide, else the space the content belongs to.
    A second active job for the connection raises ``JobAlreadyActiveError``
    (unique partial index).
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                "INSERT INTO ingestion_jobs (org_id, connection_id, status, workspace_id) "
                "VALUES (%s, %s, 'queued', %s) RETURNING id::text",
                (org_id, connection_id, workspace_id),
            ).fetchone()
    except DatabaseError as exc:
        # Parallel POST /ingest: unique partial index on active jobs.
        if isinstance(exc.__cause__, UniqueViolation) or "idx_ingestion_jobs_one_active" in str(
            exc
        ):
            raise JobAlreadyActiveError(
                "A sync is already in progress for this connection"
            ) from exc
        raise
    return row[0]


def claim_next() -> IngestionJob | None:
    """Atomically claim the oldest queued job, marking it ``running``.

    One statement, so two workers never claim the same job. ``attempts``
    counts at claim: a job that OOM-kills its process never reaches a later
    write, and the requeue cap depends on it.
    """
    with get_connection() as conn:
        row = conn.execute(
            f"""
            UPDATE ingestion_jobs
            SET status = 'running', started_at = now(), attempts = attempts + 1
            WHERE id = (
                SELECT id FROM ingestion_jobs
                WHERE status = 'queued'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING {_SELECT_COLUMNS}
            """
        ).fetchone()
    return _row_to_job(row) if row else None


def update_progress(
    job_id: str,
    *,
    phase: str | None = None,
    processed: int | None = None,
    total: int | None = None,
) -> None:
    """Record live progress on a ``running`` job; only the fields passed.

    Best-effort: a failed write must never abort a run (worst case, a stale
    spinner).
    """
    sets: list[str] = []
    params: list[object] = []
    if phase is not None:
        sets.append("phase = %s")
        params.append(phase)
    if processed is not None:
        sets.append("processed_documents = %s")
        params.append(processed)
    if total is not None:
        sets.append("total_documents = %s")
        params.append(total)
    if not sets:
        return

    # The heartbeat tells reap_stuck the job is alive.
    sets.append("progress_at = now()")

    params.append(job_id)
    try:
        with get_connection() as conn:
            conn.execute(
                f"UPDATE ingestion_jobs SET {', '.join(sets)} WHERE id = %s",
                tuple(params),
            )
    except Exception:  # noqa: BLE001 - progress is observability, never load-bearing
        return


def mark_succeeded(
    job_id: str, doc_count: int, permission_unreadable: int = 0
) -> None:
    """Mark the job done. ``permission_unreadable`` (files whose sharing could
    not be read) is written on success too: that is the case it describes."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE ingestion_jobs SET status = 'succeeded', doc_count = %s, "
            "permission_unreadable_documents = %s, "
            "finished_at = now() WHERE id = %s",
            (doc_count, permission_unreadable, job_id),
        )


def mark_failed(job_id: str, error: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE ingestion_jobs SET status = 'failed', error = %s, "
            "finished_at = now() WHERE id = %s",
            (error, job_id),
        )



def requeue_interrupted_running(
    max_attempts: int = DEFAULT_MAX_JOB_ATTEMPTS,
) -> int:
    """Return orphaned ``running`` jobs to ``queued`` after a crash or reload.

    Safe: an incremental ingest skips pages already stored. Capped at
    ``max_attempts``, past which the job is ``failed``: a job that OOMs the
    process would otherwise crash every boot forever (two real incidents).
    Returns the number requeued.
    """
    with get_connection() as conn:
        exhausted = conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = 'failed',
                finished_at = now(),
                error = 'Abandoned after ' || attempts || ' interrupted attempts'
                        || ' — the worker process did not survive this job'
                        || ' (see INGEST_MAX_JOB_ATTEMPTS).'
            WHERE status = 'running' AND attempts >= %s
            RETURNING id
            """,
            (max_attempts,),
        ).fetchall()

        rows = conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = 'queued',
                started_at = NULL,
                error = NULL
            WHERE status = 'running'
            RETURNING id
            """
        ).fetchall()

    if exhausted:
        logger.error(
            "Abandoned %s ingestion job(s) that repeatedly failed to complete "
            "(>= %s attempts) — refusing to requeue them again",
            len(exhausted),
            max_attempts,
        )
    return len(rows)


def reap_stuck(timeout_minutes: int = DEFAULT_REAP_TIMEOUT_MINUTES) -> int:
    """Flip any silent ``running`` job to ``failed``. Returns how many.

    Measures silence (``coalesce(progress_at, started_at)``), not age: by age
    alone a healthy long ingest was failed while still working.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = 'failed',
                error = 'worker timeout (no progress reported)',
                finished_at = now()
            WHERE status = 'running'
              AND coalesce(progress_at, started_at)
                  < now() - (%s || ' minutes')::interval
            RETURNING id
            """,
            (timeout_minutes,),
        ).fetchall()
    return len(rows)


def get_job(org_id: str, job_id: str) -> IngestionJob | None:
    """One job, scoped to ``org_id``: another org's job does not exist."""
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM ingestion_jobs WHERE id = %s AND org_id = %s",
            (job_id, org_id),
        ).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(org_id: str, workspace_id: str | None = None) -> list[IngestionJob]:
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM ingestion_jobs "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "ORDER BY created_at DESC",
            (org_id, workspace_id),
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def get_connection_provider(connection_id: str, org_id: str) -> str:
    """The connection's ``provider``, scoped to ``org_id``; raises
    ``ConfigurationError`` when this org has no such connection.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT provider FROM oauth_connections WHERE id = %s AND org_id = %s",
            (connection_id, org_id),
        ).fetchone()
    if not row:
        raise ConfigurationError(
            f"No connection {connection_id!r} for this organization."
        )
    return row[0]
