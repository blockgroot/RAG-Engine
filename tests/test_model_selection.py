"""Multi-Model Selection: routing, isolation, and the cache key.

These assert the properties that are *silently* wrong when broken — a model
that never actually changes, a cached answer served across models, a leaked
selection between requests — rather than that the wiring exists.
"""

from __future__ import annotations

import pytest

from app.config.settings import OpenRouterSettings
from app.llm import catalog
from app.llm.base import ChatResult, LLMProvider
from app.llm.routed import (
    RoutedLLMProvider,
    answering_model,
    selected_model,
    use_model,
)
from app.rag.query_cache import _question_hash


class _Recording(LLMProvider):
    """Stands in for a real endpoint; records the model it was asked for."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.calls: list[str] = []
        self.last_resolved_model: str | None = None

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.calls.append(prompt)
        self.last_resolved_model = self.model
        return f"answered by {self.model}"

    def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
        self.last_resolved_model = self.model
        return ChatResult(text="ok", tool_calls=[])


@pytest.fixture(autouse=True)
def _reset_selection():
    """Every test starts on the default path and leaves it that way."""
    use_model(None)
    yield
    use_model(None)


@pytest.fixture
def routed():
    default = _Recording("default-model")
    provider = RoutedLLMProvider(
        default,
        settings=OpenRouterSettings(api_key="test-key", base_url="http://openrouter.test"),
    )
    return provider, default


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------
def test_no_selection_uses_the_default_provider(routed):
    """The whole safety story: untouched dropdown == today's behaviour."""
    provider, default = routed
    assert provider.generate("hi") == "answered by default-model"
    assert default.calls == ["hi"]


def test_auto_is_identical_to_no_selection(routed):
    provider, default = routed
    use_model(catalog.AUTO)
    assert selected_model() is None
    assert provider.generate("hi") == "answered by default-model"


def test_selection_routes_away_from_the_default(routed):
    """A picked model must not quietly answer from the default anyway."""
    provider, default = routed
    picked = catalog.MODELS[0].id
    use_model(picked)
    assert provider.active().model == picked
    assert default.calls == []


def test_client_is_built_once_per_model(routed):
    """Cached per model — a new client per request would leak connections."""
    provider, _ = routed
    use_model(catalog.MODELS[0].id)
    first = provider.active()
    second = provider.active()
    assert first is second


def test_falls_back_to_default_when_openrouter_unconfigured():
    """A stray model on a deployment without a key must not break chat."""
    default = _Recording("default-model")
    provider = RoutedLLMProvider(default, settings=OpenRouterSettings(api_key=None))
    use_model(catalog.MODELS[0].id)
    assert provider.generate("hi") == "answered by default-model"


# --------------------------------------------------------------------------
# Reporting which model answered
# --------------------------------------------------------------------------
def test_answering_model_is_recorded_on_generate(routed):
    provider, _ = routed
    provider.generate("hi")
    assert answering_model() == "default-model"


def test_answering_model_resets_between_requests(routed):
    """A pooled thread must not report the previous request's model."""
    provider, _ = routed
    provider.generate("hi")
    assert answering_model() is not None
    use_model(None)  # next request begins
    assert answering_model() is None


# --------------------------------------------------------------------------
# Cache isolation — a wrong key here is a WRONG ANSWER, not a slow one
# --------------------------------------------------------------------------
def test_cache_key_differs_per_model():
    base = _question_hash("what is the leave policy")
    use_model(catalog.MODELS[0].id)
    picked = _question_hash("what is the leave policy")
    assert base != picked


def test_cache_key_unchanged_for_default_path():
    """Pre-existing cache entries must stay readable after this feature."""
    base = _question_hash("what is the leave policy")
    use_model(catalog.AUTO)
    assert _question_hash("what is the leave policy") == base


def test_two_models_do_not_share_a_cache_slot():
    keys = set()
    for choice in catalog.MODELS[:2]:
        use_model(choice.id)
        keys.add(_question_hash("what is the leave policy"))
    assert len(keys) == 2


# --------------------------------------------------------------------------
# Validation at the trust boundary
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "", "auto", catalog.MODELS[0].id])
def test_selectable_accepts_auto_blank_and_catalogued_ids(value):
    assert catalog.is_selectable(value)


@pytest.mark.parametrize(
    "value",
    ["openai/gpt-4o", "../../etc/passwd", "deepseek/deepseek-chat-v3.1"],
)
def test_selectable_rejects_anything_uncatalogued(value):
    """An uncatalogued id must never reach an outbound call or a cache key."""
    assert not catalog.is_selectable(value)


def test_aux_provider_is_not_routable():
    """Ingest contextualization must never follow a member's model choice.

    Guaranteed structurally — the aux factory does not wrap — because chunks
    authored by different models would end up ranked against each other in one
    index.
    """
    from app.llm.factory import build_aux_llm_provider

    import app.llm.factory as factory_module
    source = __import__("inspect").getsource(factory_module.build_aux_llm_provider)
    assert "RoutedLLMProvider" not in source


# --------------------------------------------------------------------------
# Schedulers — the report surface
# --------------------------------------------------------------------------
def test_scheduler_run_applies_its_own_model(monkeypatch):
    """A worker runs schedulers back to back in one long-lived process.

    If the model were applied once at startup rather than per run, the first
    scheduler's pick would silently generate every later scheduler's report.
    """
    from datetime import datetime, timezone

    from app.schedulers import runner
    from app.schedulers.activity import ActivityDigest, ActivityItem
    from app.schedulers.store import Scheduler

    seen: list[str | None] = []

    class _Probe:
        def generate(self, prompt, **kwargs):
            seen.append(selected_model())
            return "MODE: A\n\nreport"

    def _scheduler(model: str | None) -> Scheduler:
        now = datetime.now(timezone.utc)
        return Scheduler(
            id="s", org_id="o", user_id="u", connection_id="c", provider="slack",
            frequency="weekly", prompt="p", status="active", last_run_at=now,
            next_run_at=now, attempts=0, last_error=None, created_at=now,
            model=model,
        )

    monkeypatch.setattr(
        runner, "get_user", lambda _: type("U", (), {"email": "a@b.c"})()
    )
    monkeypatch.setattr(
        runner,
        "fetch_activity",
        lambda *a, **k: ActivityDigest(items=(ActivityItem(summary="x"),), text="x"),
    )
    monkeypatch.setattr(
        runner.reports, "save_report", lambda **kw: type("R", (), {"id": "r"})()
    )
    monkeypatch.setattr(
        runner, "send_scheduler_report_email_safe", lambda *a, **k: False
    )

    picked = catalog.MODELS[0].id
    runner.run_scheduler_once(_scheduler(picked), llm=_Probe())
    runner.run_scheduler_once(_scheduler(None), llm=_Probe())

    assert seen == [picked, None], (
        "each run must use its own scheduler's model, not the previous one's"
    )
