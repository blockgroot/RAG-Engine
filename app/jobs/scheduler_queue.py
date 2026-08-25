"""Claim-based queue over the ``schedulers`` table.

Same idiom as ``app/jobs/queue.py`` — ``UPDATE ... WHERE id IN (SELECT ...
FOR UPDATE SKIP LOCKED)`` — so concurrent workers can never run the same
scheduler twice, with no new infrastructure.

One structural difference from ``ingestion_jobs``: a scheduler is a *due
list*, not a work list. A claimed row is not consumed; on success its
``next_run_at`` advances by its own interval and it returns to ``active``
for the next cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..db.connection import get_connection
from ..schedulers.store import COLUMNS, Scheduler, interval_for, row_to_scheduler

logger = logging.getLogger(__name__)

# How long a failing scheduler waits before its next attempt. Short relative
# to the weekly/monthly cadence, so a transient outage (API down, LLM
# rate-limited) still delivers the report the same day rather than skipping
# the cycle entirely.
RETRY_BACKOFF = "1 hour"


def claim_due(limit: int = 5) -> list[Scheduler]:
    """Claim up to ``limit`` due schedulers, flipping them to ``running``.

    Claim and status flip are one statement, so two workers polling at the
    same moment cannot both pick up the same scheduler. ``attempts`` is
    incremented here, at claim time, for the same reason
    ``queue.claim_next`` does it: a run that kills the process never reaches
    a later write, so counting on completion would leave exactly the rows
    that need bounding at zero forever.
    """
    # The row set is selected in a CTE, not an `id IN (SELECT ... LIMIT n)`
    # subquery: with `IN`, Postgres is free to re-evaluate the subquery and the
    # LIMIT does not bind the number of rows updated (measured — a limit of 2
    # claimed all 5 due rows). queue.py's `id = (SELECT ... LIMIT 1)` is safe
    # only because a scalar subquery is evaluated once; claiming several needs
    # this form.
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

    ``next_run_at`` advances from ``now()``, not from the previous
    ``next_run_at``: a scheduler that ran late (worker down, retries) should
    not immediately fire again to "catch up" — the next window starts when
    this report actually covered up to.

    ``last_run_at`` is set to ``covered_until`` — **the instant the run began,
    stamped by the caller before it fetched** — and NOT to ``now()``.
    ``last_run_at`` IS the next window's start (``runner.window_start``), so
    delivery time would skip everything that happened while the report was
    generated and mailed: an LLM call plus an SMTP round trip is tens of
    seconds, and a message posted in that hole appeared in no report at all.
    Stamping before the fetch means the boundary can only ever *overlap* by
    the few hundred milliseconds between the stamp and the API read — a
    visible duplicate rather than a silent loss, which is the direction this
    codebase takes everywhere else (CLAUDE.md §2, "mark truncation").

    Falls back to ``now()`` when no stamp is given, so an ad-hoc call still
    advances the window rather than leaving it NULL and re-fetching 7 days.
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
    """Record a failed run: retry soon, or give up past ``max_attempts``.

    Bounded for the same reason ``requeue_interrupted_running`` is: an
    unbounded retry on a scheduler that fails every time (revoked token,
    deleted channel) would poll a dead service forever. Past the cap the row
    goes ``failed`` and stops being claimed — the user's other schedulers are
    untouched, and ``last_error`` explains why this one stopped.

    ``last_run_at`` is deliberately NOT advanced on failure, so the next
    successful run still covers the whole window since the last delivered
    report rather than silently dropping the activity in between.
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

    Bounded by ``max_attempts`` for the same reason
    ``queue.requeue_interrupted_running`` is: a run that *kills the process*
    never reaches ``mark_run_failed``, so its own cap can never fire — only
    the claim-time ``attempts`` increment survives. Without a cap here, a
    scheduler whose activity fetch OOMs the worker would be requeued,
    claimed, and crash the process again forever. Past the cap it is retired
    to ``failed`` instead. Returns how many were actually requeued.
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
