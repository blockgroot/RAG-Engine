"""Claim due schedulers and run them, one failure never touching another."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..config.settings import SchedulerSettings
from ..jobs import scheduler_queue

logger = logging.getLogger(__name__)


def run_due_schedulers_once(settings: SchedulerSettings | None = None) -> int:
    """Run every due scheduler; returns how many were processed.

    The broad ``except`` is a batch boundary: one scheduler's failure costs
    only its own cycle.
    """
    settings = settings or SchedulerSettings.from_env()
    if not settings.enabled:
        # Kill switch for every caller, manual runs included.
        return 0
    due = scheduler_queue.claim_due(limit=settings.batch_size)
    if not due:
        return 0

    # Lazy: the runner pulls in the LLM and source readers.
    from .runner import run_scheduler_once

    for scheduler in due:
        # Stamped before the run: the next window starts here, so the run's
        # own latency is covered by some report.
        covered_until = datetime.now(timezone.utc)
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
            scheduler_queue.mark_run_success(
                scheduler.id, scheduler.frequency, covered_until=covered_until
            )

    return len(due)
