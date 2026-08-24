"""Claim due schedulers and run them, one failure never touching another.

Split from ``runner.py`` on purpose: this module owns claiming and status
bookkeeping, ``runner`` owns doing the work. That boundary is what makes
per-scheduler isolation a single obvious ``try/except`` here rather than
something threaded through the run itself.
"""

from __future__ import annotations

import logging

from ..config.settings import SchedulerSettings
from ..jobs import scheduler_queue

logger = logging.getLogger(__name__)


def run_due_schedulers_once(settings: SchedulerSettings | None = None) -> int:
    """Run every currently-due scheduler. Returns how many were processed.

    Each scheduler is wrapped individually, so a revoked Slack token, a
    deleted channel, or an LLM outage on one costs *only* that scheduler its
    cycle — every other one in the same batch still runs. That is a stated
    requirement of the feature, and it is cheap to guarantee here because
    the queue already records failure per row.

    The broad ``except Exception`` is deliberate rather than sloppy: this is
    a batch boundary. Narrowing it would let an unanticipated error type
    abort the remaining schedulers in the batch, which is exactly the
    coupling this function exists to prevent.
    """
    settings = settings or SchedulerSettings.from_env()
    if not settings.enabled:
        # Guarded here, not only in the two worker loops' timers, so the
        # kill-switch holds for every caller — including a manual invocation
        # from a shell, which is how these get triggered during a demo.
        return 0
    due = scheduler_queue.claim_due(limit=settings.batch_size)
    if not due:
        return 0

    # Import here rather than at module load: the runner pulls in the LLM
    # factory and the source readers, and this module is imported by the API
    # process at startup (see app/api/main.py) where that cost buys nothing
    # until a scheduler is actually due.
    from .runner import run_scheduler_once

    for scheduler in due:
        try:
            run_scheduler_once(scheduler)
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning(
                "Scheduler %s (%s, %s) failed: %s",
                scheduler.id,
                scheduler.provider,
                scheduler.frequency,
                exc,
            )
            scheduler_queue.mark_run_failed(
                scheduler.id, str(exc), max_attempts=settings.max_attempts
            )
        else:
            scheduler_queue.mark_run_success(scheduler.id, scheduler.frequency)

    return len(due)
