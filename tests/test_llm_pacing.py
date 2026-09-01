"""Background LLM work must not eat a live question's rate limit.

No DB, no network: the subject is a 60-second window and who is allowed to
spend it. Every test resets the process-global window first, because that is
exactly what it is — process-global.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.config.settings import LLMPacingSettings
from app.llm import pacing


@pytest.fixture(autouse=True)
def _clean_window():
    pacing.reset_for_tests()
    yield
    pacing.reset_for_tests()


SETTINGS = LLMPacingSettings(
    enabled=True, max_rpm=15, reserve_rpm=5, max_wait_seconds=0.3
)


def test_an_idle_window_lets_background_work_through_immediately():
    started = time.monotonic()
    assert pacing.wait_for_background_slot(SETTINGS) is True
    assert time.monotonic() - started < 0.1, "an idle window must not sleep"


def test_background_is_refused_once_it_would_touch_the_reserve():
    """The reserve IS the guarantee: with max_rpm=15 and reserve_rpm=5,
    background gets 10 and the 11th must not proceed."""
    for _ in range(10):
        pacing.record()

    assert pacing.calls_in_window() == 10
    assert pacing.wait_for_background_slot(SETTINGS) is False


def test_the_reserve_is_still_free_when_background_is_refused():
    """The point of refusing background is that a person can still get through.
    Interactive calls never consult the pacer, so this asserts the invariant
    that matters: we stopped short of the limit, not at it."""
    for _ in range(10):
        pacing.record()

    assert pacing.wait_for_background_slot(SETTINGS) is False
    # 5 requests of the minute remain unspent — the answer path's headroom.
    assert SETTINGS.max_rpm - pacing.calls_in_window() == 5


def test_interactive_calls_count_toward_the_window():
    """Recording is what makes the reserve real. If only background calls were
    counted, live traffic would be invisible and the reserve would protect
    nothing."""
    from app.llm.metering import log_llm_call

    class _Provider:
        model = "test-model"
        last_usage = None

    log_llm_call("generate", _Provider())
    assert pacing.calls_in_window() == 1


def test_a_freed_slot_lets_background_resume():
    """Not a timeout test: the window is time-based, so proving recovery needs
    the timestamps to age out. Ages them directly rather than sleeping 60s."""
    for _ in range(10):
        pacing.record()
    assert pacing.wait_for_background_slot(SETTINGS) is False

    # Shift every recorded call a full window into the past.
    with pacing._lock:  # noqa: SLF001 - white-box on purpose, see docstring
        pacing._calls = type(pacing._calls)(t - 61.0 for t in pacing._calls)

    assert pacing.wait_for_background_slot(SETTINGS) is True
    assert pacing.calls_in_window() == 0


def test_disabled_pacing_never_blocks():
    off = LLMPacingSettings(enabled=False, max_rpm=15, reserve_rpm=5)
    for _ in range(100):
        pacing.record()
    assert pacing.wait_for_background_slot(off) is True


def test_a_reserve_at_or_above_the_limit_is_clamped(monkeypatch):
    """A misconfigured reserve must not silently starve background work to
    zero — it would look like contextualization had simply stopped working."""
    monkeypatch.setenv("LLM_MAX_RPM", "10")
    monkeypatch.setenv("LLM_RESERVE_RPM", "50")
    settings = LLMPacingSettings.from_env()
    assert settings.reserve_rpm == 9
    assert settings.max_rpm - settings.reserve_rpm == 1, "one slot must remain"


def test_recording_is_thread_safe():
    """Contextualization runs on a ThreadPoolExecutor, so concurrent record()
    is the normal case, not an edge one."""
    def _spam():
        for _ in range(200):
            pacing.record()

    threads = [threading.Thread(target=_spam) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert pacing.calls_in_window() == 800


def test_contextualize_degrades_instead_of_spending_a_reserved_slot(monkeypatch):
    """End to end through the real caller: a refused slot returns the bare
    chunk and never calls the LLM at all."""
    from app.ingestion import contextualize

    calls: list[str] = []

    class _LLM:
        model = "test-model"
        last_usage = None

        def generate(self, prompt, **kwargs):
            calls.append(prompt)
            return "context line"

    monkeypatch.setattr(contextualize, "wait_for_background_slot", lambda: False)

    out = contextualize.contextualize_chunk(_LLM(), "doc text", "chunk text")

    assert out == "chunk text", "must return the chunk unchanged"
    assert calls == [], "must not spend an LLM request it was refused"
