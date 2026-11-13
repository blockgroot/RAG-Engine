"""Deferred running-summary fold.

The fold is bookkeeping after the answer is decided, so it runs on a
single-worker executor (FIFO keeps one conversation's folds ordered without
chaining ``Future.result()``, which can deadlock). The next turn's rewrite
waits on THIS conversation's pending fold (``wait_for_conversation_fold``),
so a turn leaving the verbatim window is never invisible. Best-effort:
failures are logged. ``shutdown_summary_folds`` drains on exit and in tests.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()
# Latest Future for each conversation (for the rewrite barrier).
_pending: dict[str, Future[None]] = {}
# All in-flight futures (for process-wide drain on shutdown / tests).
_all_futures: set[Future[None]] = set()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            # One worker: FIFO queue keeps per-conversation folds ordered without
            # chaining Future.result() inside workers (that pattern deadlocks).
            _executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="summary-fold"
            )
        return _executor


def schedule_summary_fold(
    conversation_id: str, fold: Callable[[], None]
) -> Future[None]:
    """Run ``fold`` in the background (ordered FIFO with other folds)."""
    executor = _get_executor()

    def _run() -> None:
        try:
            fold()
        except Exception:  # noqa: BLE001 — never fail the request path
            logger.exception(
                "Background summary fold failed for conversation %s",
                conversation_id,
            )

    fut: Future[None] = executor.submit(_run)
    with _lock:
        _pending[conversation_id] = fut
        _all_futures.add(fut)

    def _cleanup(done: Future[None], cid: str = conversation_id) -> None:
        with _lock:
            _all_futures.discard(done)
            if _pending.get(cid) is done:
                _pending.pop(cid, None)

    fut.add_done_callback(_cleanup)
    return fut


def wait_for_conversation_fold(
    conversation_id: str, timeout: float | None = None
) -> None:
    """Block until any in-flight fold for ``conversation_id`` finishes."""
    with _lock:
        fut = _pending.get(conversation_id)
    if fut is None:
        return
    try:
        fut.result(timeout=timeout)
    except Exception:  # noqa: BLE001 — best-effort barrier
        pass


def wait_for_pending_summary_folds(timeout: float | None = 30.0) -> None:
    """Drain every in-flight summary fold (tests + shutdown)."""
    with _lock:
        futures = list(_all_futures)
    for fut in futures:
        try:
            fut.result(timeout=timeout)
        except Exception:  # noqa: BLE001 — best-effort drain
            pass


def shutdown_summary_folds(*, wait: bool = True, timeout: float = 30.0) -> None:
    """Drain pending folds and stop the executor. Safe to call multiple times."""
    global _executor
    if wait:
        wait_for_pending_summary_folds(timeout=timeout)
    with _lock:
        ex = _executor
        _executor = None
        _pending.clear()
        _all_futures.clear()
    if ex is not None:
        ex.shutdown(wait=False, cancel_futures=False)
