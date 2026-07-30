"""Deferred running-summary fold (Phase 15).

The incremental summary fold (``RagPipeline._update_running_summary``) is pure
bookkeeping after the answer is already decided — nothing in ``RagResult``
depends on it. Running it synchronously inside ``answer()`` made every
conversational turn past the verbatim window pay a full LLM round-trip before
the caller (and any SSE/CLI stream) saw a single character.

This module schedules the fold on a **single-worker** background executor so
``answer()`` can return immediately and folds for a given conversation stay
strictly ordered (the executor's queue is FIFO — no worker-side
``Future.result()`` chaining, which can deadlock a thread pool).

A barrier before the next turn's rewrite (``wait_for_conversation_fold``) waits
**specifically on this conversation's own outstanding fold Future** — looked up
in ``_pending[conversation_id]`` — not on the shared executor's queue in general
and not on other conversations' folds. (A single-worker FIFO executor means an
earlier-queued fold for a *different* conversation can still delay when this
Future actually runs; that is scheduler ordering, not the barrier keying on
global queue state.) This keeps a turn that left the verbatim window from being
invisible to both the summary *and* recent turns.

Folds remain best-effort (exceptions are logged, never raised to the caller).
``shutdown_summary_folds`` / ``wait_for_pending_summary_folds`` drain work on
process exit and in tests so a mid-shutdown drop is unlikely without a durable
job queue (deliberately out of scope here).
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
