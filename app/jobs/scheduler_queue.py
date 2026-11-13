"""Claim-based queue over the ``schedulers`` table (``FOR UPDATE SKIP LOCKED``).

A due list, not a work list: a claimed row is not consumed; on success its
``next_run_at`` advances and it returns to ``active``.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..db.connection import get_connection
from ..schedulers.store import COLUMNS, Scheduler, interval_for, row_to_scheduler

logger = logging.getLogger(__name__)

# Short, so a transient outage still delivers the report the same day.
RETRY_BACKOFF = "1 hour"


def claim_due(limit: int = 5) -> list[Scheduler]:
    """Claim up to ``limit`` due schedulers, flipping them to ``running``.

    One statement, so two workers never claim the same row. ``attempts``
    counts at claim: a run that kills the process never reaches a later write.
    """
    # A CTE, not `id IN (SELECT ... LIMIT n)`: with IN the LIMIT does not bound
    # the UPDATE (a limit of 2 claimed all 5 due rows).
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            WITH due AS (
                SELECT id FROM schedulers
                WHERE status = 'active' AND next_run_at <= now()
                ORDER BY next_run_at
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE schedulers AS s
            SET status = 'running', attempts = s.attempts + 1
            FROM due
            WHERE s.id = due.id
            RETURNING {", ".join("s." + c.strip() for c in COLUMNS.split(","))}
            """,
            (limit,),
        ).fetchall()
    return [row_to_scheduler(row) for row in rows]


def mark_run_success(
    scheduler_id: str, frequency: str, covered_until: datetime | None = None
) -> None:
    """Record a completed run and schedule the next one.

    ``next_run_at`` advances from ``now()``, so a late run does not fire again
    to catch up. ``last_run_at`` is ``covered_until`` (stamped before the
    fetch), not delivery time, or activity during generation would land in no
    report; the boundary may overlap, never gap. Falls back to ``now()``.
    """
    interval = interval_for(frequency)
    with get_connection() as conn:
        conn.execute(
            "UPDATE schedulers SET status = 'active', "
            "last_run_at = coalesce(%s, now()), "
            "next_run_at = now() + %s::interval, attempts = 0, last_error = NULL "
            "WHERE id = %s",
            (covered_until, interval, scheduler_id),
        )


def mark_run_failed(scheduler_id: str, error: str, max_attempts: int = 3) -> None:
    """Record a failed run: retry soon, or go ``failed`` past ``max_attempts``
    so a dead service is not polled forever. ``last_run_at`` is not advanced,
    so the next success covers the whole gap.
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            UPDATE schedulers
            SET status = CASE WHEN attempts >= %s THEN 'failed' ELSE 'active' END,
                next_run_at = CASE WHEN attempts >= %s
                                   THEN next_run_at
                                   ELSE now() + %s::interval END,
                last_error = %s
            WHERE id = %s
            RETURNING status
            """,
            (max_attempts, max_attempts, RETRY_BACKOFF, error[:2000], scheduler_id),
        ).fetchone()
    if row and row[0] == "failed":
        logger.error(
            "Scheduler %s abandoned after %s failed attempts: %s",
            scheduler_id,
            max_attempts,
            error,
        )


def requeue_interrupted_running(max_attempts: int = 3) -> int:
    """Return orphaned ``running`` schedulers to ``active`` after a crash.

    Capped: a run that kills the process never reaches ``mark_run_failed``,
    so without this an OOMing scheduler would crash the worker forever.
    Returns how many were requeued.
    """
    with get_connection() as conn:
        exhausted = conn.execute(
            """
            UPDATE schedulers
            SET status = 'failed',
                last_error = 'Abandoned after ' || attempts || ' interrupted '
                             || 'attempts — the worker did not survive this run.'
            WHERE status = 'running' AND attempts >= %s
            RETURNING id
            """,
            (max_attempts,),
        ).fetchall()

        rows = conn.execute(
            "UPDATE schedulers SET status = 'active' WHERE status = 'running' "
            "RETURNING id"
        ).fetchall()

    if exhausted:
        logger.error(
            "Abandoned %s scheduler(s) that repeatedly failed to complete "
            "(>= %s attempts) — refusing to requeue them again",
            len(exhausted),
            max_attempts,
        )
    return len(rows)
