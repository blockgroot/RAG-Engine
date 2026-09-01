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


# --------------------------------------------------------------------------
# The structural fix: a separate endpoint for background work
# --------------------------------------------------------------------------


def test_a_separate_aux_endpoint_skips_the_gate_entirely(monkeypatch):
    """Separate endpoints cannot contend, so there is nothing to yield to.
    Throttling here would cost real quality (un-prefixed chunks) to protect a
    limit background work never touches."""
    monkeypatch.setenv("LLM_AUX_BASE_URL", "https://integrate.api.example.com/v1")
    monkeypatch.setenv("LLM_AUX_API_KEY", "aux-key")

    for _ in range(50):  # far past any budget
        pacing.record()

    assert pacing.wait_for_background_slot(SETTINGS) is True


def test_a_half_configured_aux_endpoint_still_paces(monkeypatch):
    """base_url without a key would send the main credential to a foreign host
    -- a 401 on every contextualization, degrading silently. Half-configured
    must mean "not configured", so the gate stays on."""
    monkeypatch.setenv("LLM_AUX_BASE_URL", "https://integrate.api.example.com/v1")
    monkeypatch.delenv("LLM_AUX_API_KEY", raising=False)

    for _ in range(10):
        pacing.record()

    assert pacing.wait_for_background_slot(SETTINGS) is False


def test_a_key_without_a_base_url_still_paces(monkeypatch):
    """The mirror case: a foreign key sent to the main endpoint."""
    monkeypatch.delenv("LLM_AUX_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_AUX_API_KEY", "aux-key")

    for _ in range(10):
        pacing.record()

    assert pacing.wait_for_background_slot(SETTINGS) is False


def test_aux_provider_uses_its_own_endpoint_when_both_are_set(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "main-model")
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://main.example.com/v1")
    monkeypatch.setenv("LLM_AUX_BASE_URL", "https://aux.example.com/v1")
    monkeypatch.setenv("LLM_AUX_API_KEY", "aux-key")

    from app.llm.factory import build_aux_llm_provider

    provider = build_aux_llm_provider()
    assert provider.base_url == "https://aux.example.com/v1"
    assert provider.api_key == "aux-key"


def test_aux_provider_falls_back_to_the_main_endpoint_by_default(monkeypatch):
    """Unset must be byte-identical to the behaviour before these settings
    existed -- this is the configuration every current deploy runs."""
    monkeypatch.setenv("LLM_MODEL", "main-model")
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://main.example.com/v1")
    monkeypatch.delenv("LLM_AUX_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_AUX_API_KEY", raising=False)

    from app.llm.factory import build_aux_llm_provider

    provider = build_aux_llm_provider()
    assert provider.base_url == "https://main.example.com/v1"
    assert provider.api_key == "main-key"


def test_a_half_configured_aux_provider_never_mixes_credentials(monkeypatch):
    """The silent-401 case, asserted on the provider itself: a foreign
    base_url must NOT be paired with the main key."""
    monkeypatch.setenv("LLM_MODEL", "main-model")
    monkeypatch.setenv("LLM_API_KEY", "main-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://main.example.com/v1")
    monkeypatch.setenv("LLM_AUX_BASE_URL", "https://aux.example.com/v1")
    monkeypatch.delenv("LLM_AUX_API_KEY", raising=False)

    from app.llm.factory import build_aux_llm_provider

    provider = build_aux_llm_provider()
    assert provider.base_url == "https://main.example.com/v1"
    assert provider.api_key == "main-key"
