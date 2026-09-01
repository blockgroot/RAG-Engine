"""Keep background LLM work from eating a live question's quota.

The problem this exists for
--------------------------
``build_aux_llm_provider`` shares ``api_key`` and ``base_url`` with the main
provider *by default*, so ingest contextualization and a member's question draw
from the SAME rate limit. The free Gemini tier is **15 requests per minute**. Ingest
was previously only ever started by a human pressing Update, so the collision
was rare and self-inflicted; now that connections sync unattended every few
hours, a background enrichment run can be in flight at the exact moment
someone asks a question — and a 429 on the answer path is a *failed answer*,
not a slow one.

The shape of the fix
--------------------
Only the BACKGROUND lane is throttled. Interactive calls never wait on
anything here — they only *record* that they happened, which is an append
under a lock held for microseconds. Making a user's request block on a
semaphore to protect a batch job would trade the failure for a worse one.

``reserve_rpm`` is the actual guarantee: background work refuses to consume
the last N requests of each minute, so that many are always available to a
person. It is a reservation, not a prediction — nothing has to estimate
demand.

When this module does nothing at all
-----------------------------------
Setting ``LLM_AUX_BASE_URL`` **and** ``LLM_AUX_API_KEY`` moves ingest onto its
own endpoint, and separate endpoints cannot contend — so the gate is skipped
entirely. That is the structural fix; everything above is the managed
approximation for deployments running on one key. Prefer the structural one:
throttling background work against a limit it does not touch trades real
quality (un-prefixed chunks) for nothing.

Known ceiling
-------------
The window is **per process**. With ``INGEST_WORKER_IN_API=true`` (the deploy
default) there is one process and the accounting is exact. Running
``scripts/run_worker.py`` separately gives each process its own window, so the
effective background ceiling doubles.

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

    Called from ``log_llm_call``, which every LLM call in the codebase already
    goes through — so there is no call site that can forget to report, and no
    new plumbing threaded through the pipeline.

    Counts aux calls too, even when aux is on its own endpoint and they consume
    no main quota. Harmless rather than wrong: interactive calls are never
    gated, and the background gate is skipped entirely in that configuration —
    so the only reader of an inflated count is a diagnostic. Teaching this
    function which endpoint a provider used would mean threading that through
    every call site, which is the plumbing the single choke point exists to
    avoid.
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

    "Safe" means the last 60 seconds hold fewer than ``max_rpm -
    reserve_rpm`` calls, leaving the reserve free for whoever is actually
    waiting on a screen.

    Returns ``False`` rather than raising on timeout: the one caller
    (``contextualize_chunk``) already treats a missing context prefix as an
    acceptable degradation, and blocking an ingest indefinitely to win a race
    against live traffic is the wrong trade — a chunk without its prefix is
    still a usable chunk, while a wedged ingest job is a stuck queue.
    """
    settings = settings or LLMPacingSettings.from_env()
    if not settings.enabled or settings.max_rpm <= 0:
        return True

    # Separate endpoints cannot contend, so there is nothing to yield to. This
    # is the structural fix that this whole module only approximates: with
    # LLM_AUX_BASE_URL + LLM_AUX_API_KEY set, ingest draws from its own rate
    # limit and throttling it against the main window would cost quality
    # (un-prefixed chunks) to protect a limit it never touches.
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
            # Sleep only until the oldest call leaves the window: that is the
            # exact moment a slot frees, so this neither spins nor overshoots.
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
