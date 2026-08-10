"""Postgres-backed durable job queue for admin-triggered ingestion (Phase 12).

Why Postgres instead of an in-process background task or a new queue
dependency (Redis/Celery): an admin-triggered ingestion run can take a while
(fetch + chunk + embed a whole workspace) and must survive an API-process
restart with a truthful status — an in-memory task loses that the moment the
process recycles. We already run Postgres for everything else, so
``SELECT ... FOR UPDATE SKIP LOCKED`` gives an at-least-once, crash-safe queue
with zero new infrastructure, matching CLAUDE.md §1's self-hosted /
minimal-dependency principle.

Every function takes ``org_id`` where the query needs it and is scoped
accordingly — an admin can only ever enqueue/poll their OWN org's jobs
(enforced by the caller passing the session's ``org_id``, never a client value;
see ``app/api/admin.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.errors import UniqueViolation

from ..core.exceptions import ConfigurationError
from ..db.connection import DatabaseError, get_connection

DEFAULT_REAP_TIMEOUT_MINUTES = 30


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
    # Live progress, written as the run proceeds rather than only at the end.
    # ``doc_count`` stays the terminal "pages written this run" figure; these
    # three are what a poller can watch change while ``status`` is still
    # ``running``.
    phase: str | None = None
    total_documents: int | None = None
    processed_documents: int = 0


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
    )


_SELECT_COLUMNS = (
    "id::text, org_id::text, connection_id::text, status, doc_count, error, "
    "started_at, finished_at, created_at, workspace_id::text, "
    "phase, total_documents, processed_documents"
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

    ``workspace_id`` (Workspace-within-a-Workspace): ``None`` (default) is
    today's org-wide admin-triggered job, unchanged. Non-``None`` records
    which sub-workspace this job's fetched content should be stored under.

    Refuses a second active job for the same connection (unique partial index
    ``idx_ingestion_jobs_one_active_per_connection``) so parallel ingest POSTs
    cannot both succeed — raises ``JobAlreadyActiveError``.
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

    The claim and the status flip happen in one statement (``UPDATE ... WHERE
    id = (SELECT ... FOR UPDATE SKIP LOCKED)``) so two concurrent workers can
    never claim the same job — the row lock is held only for the instant of the
    update, not the whole ingestion run.
    """
    with get_connection() as conn:
        row = conn.execute(
            f"""
            UPDATE ingestion_jobs
            SET status = 'running', started_at = now()
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
    """Record live progress on a ``running`` job.

    Deliberately best-effort and fire-and-forget from the caller's side: a
    progress write that fails must never abort an ingestion run that is
    otherwise succeeding — the worst case is a stale spinner, not lost work.
    Only the fields passed are written, so a caller can advance the counter
    without restating the phase.
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

    params.append(job_id)
    try:
        with get_connection() as conn:
            conn.execute(
                f"UPDATE ingestion_jobs SET {', '.join(sets)} WHERE id = %s",
                tuple(params),
            )
    except Exception:  # noqa: BLE001 - progress is observability, never load-bearing
        return


def mark_succeeded(job_id: str, doc_count: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE ingestion_jobs SET status = 'succeeded', doc_count = %s, "
            "finished_at = now() WHERE id = %s",
            (doc_count, job_id),
        )


def mark_failed(job_id: str, error: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE ingestion_jobs SET status = 'failed', error = %s, "
            "finished_at = now() WHERE id = %s",
            (error, job_id),
        )


def reap_stuck(timeout_minutes: int = DEFAULT_REAP_TIMEOUT_MINUTES) -> int:
    """Flip any ``running`` job stuck past ``timeout_minutes`` to ``failed``.

    Closes the "crashed worker leaves the job running forever" gap — call this
    periodically from the worker loop (see ``app/jobs/worker.py``).
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            UPDATE ingestion_jobs
            SET status = 'failed', error = 'worker timeout', finished_at = now()
            WHERE status = 'running'
              AND started_at < now() - (%s || ' minutes')::interval
            RETURNING id
            """,
            (timeout_minutes,),
        ).fetchall()
    return len(rows)


def get_job(org_id: str, job_id: str) -> IngestionJob | None:
    """Look up one job, scoped to ``org_id`` so an admin can never poll (or even
    detect the existence of) another org's job."""
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
    """Return the ``provider`` for a connection, scoped to ``org_id``.

    Raises ``ConfigurationError`` if the connection doesn't exist *for this
    org* — never returns another org's connection's provider.
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
