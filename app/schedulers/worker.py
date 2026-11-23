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
    try:
        # A run whose process died (or hung) goes back to due: boot alone missed
        # a crash too fresh to be stale yet.
        scheduler_queue.requeue_interrupted_running(max_attempts=settings.max_attempts)
    except Exception:  # noqa: BLE001 - requeue is recovery, never the tick's failure
        logger.exception("Scheduler requeue failed")
    due = scheduler_queue.claim_due(limit=settings.batch_size)
    if not due:
        return 0

    # Lazy: the runner pulls in the LLM and source readers.
    from .runner import AlreadyDelivered, run_scheduler_once

    for scheduler in due:
        # Stamped before the run: the next window starts here, so the run's
        # own latency is covered by some report.
        covered_until = datetime.now(timezone.utc)
        try:
            run_scheduler_once(scheduler)
        except AlreadyDelivered as done:
            # Sent before a crash: record it up to what that report covered.
            logger.info("Scheduler %s: this window was already delivered", scheduler.id)
            scheduler_queue.mark_run_success(
                scheduler.id, scheduler.frequency, covered_until=done.window_end
            )
            continue
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
