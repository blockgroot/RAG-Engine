"""Keep background LLM work from eating a live question's quota.

When the aux provider shares the main key, background work and questions
draw on one rate limit, and a 429 on the answer path is a failed answer.
Only the BACKGROUND lane waits: it refuses the last ``reserve_rpm``
requests of each minute. Interactive calls only record themselves.

Setting ``LLM_AUX_BASE_URL`` and ``LLM_AUX_API_KEY`` gives background work
its own endpoint and skips the gate entirely (the structural fix).

The window is per process: a separate ``scripts/run_worker.py`` gets its
own, doubling the background ceiling.

ponytail: in-process window. Do not reach for a Postgres counter — a shared
count would cost a round trip on the hot path of every LLM call, and the
cheaper answer to a multi-process worker tripping the quota is the separate
aux endpoint above.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from ..config.settings import LLMPacingSettings, LLMSettings

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60.0

#: Monotonic timestamps of recent LLM calls, newest last.
_calls: deque[float] = deque()
_lock = threading.Lock()


def _prune(now: float) -> None:
    """Drop timestamps that have left the window. Caller holds ``_lock``."""
    cutoff = now - _WINDOW_SECONDS
    while _calls and _calls[0] <= cutoff:
        _calls.popleft()


def record() -> None:
    """Count one LLM call against the current minute.

    Called from ``log_llm_call``, which every call passes through. Aux calls
    on their own endpoint are counted too: harmless, since the gate is then
    skipped.
    """
    now = time.monotonic()
    with _lock:
        _prune(now)
        _calls.append(now)


def calls_in_window() -> int:
    """How many calls the current 60s window holds. For tests and diagnostics."""
    now = time.monotonic()
    with _lock:
        _prune(now)
        return len(_calls)


def wait_for_background_slot(settings: LLMPacingSettings | None = None) -> bool:
    """Block until a background LLM call is safe. False if it never became safe.

    Safe: the last 60 seconds hold fewer than ``max_rpm - reserve_rpm`` calls.
    False rather than raising: a chunk without its context prefix is usable,
    a wedged ingest is not.
    """
    settings = settings or LLMPacingSettings.from_env()
    if not settings.enabled or settings.max_rpm <= 0:
        return True

    # Separate endpoints cannot contend: nothing to yield to.
    if LLMSettings.from_env().aux_has_own_endpoint:
        return True

    budget = max(1, settings.max_rpm - settings.reserve_rpm)
    deadline = time.monotonic() + settings.max_wait_seconds

    while True:
        now = time.monotonic()
        with _lock:
            _prune(now)
            if len(_calls) < budget:
                return True
            # Sleep until the oldest call leaves the window: exactly when a slot frees.
            oldest = _calls[0]
        sleep_for = max(0.05, (oldest + _WINDOW_SECONDS) - now)
        if now + sleep_for > deadline:
            logger.info(
                "LLM pacing: background slot not free within %.0fs "
                "(%s calls in the last minute, budget %s) — degrading",
                settings.max_wait_seconds,
                len(_calls),
                budget,
            )
            return False
        time.sleep(sleep_for)


def reset_for_tests() -> None:
    """Clear the window. Tests only — the window is process-global state."""
    with _lock:
        _calls.clear()
