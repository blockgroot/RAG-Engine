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
def test_scheduler_reports_always_use_the_configured_model(monkeypatch):
    """A scheduled run is UNATTENDED, so it never follows a model choice.

    A chat answer from a flaky free model costs one retry the person can see.
    A failed report burns retry attempts and is noticed only when the mail
    never arrives — so reports are pinned even if something upstream left a
    selection set in this context.
    """
    from datetime import datetime, timezone

    from app.schedulers import runner
    from app.schedulers.activity import ActivityDigest, ActivityItem
    from app.schedulers.store import Scheduler

    seen: list[str | None] = []

    class _Probe:
        def generate(self, prompt, **kwargs):
            seen.append(selected_model())
            return "report"

    now = datetime.now(timezone.utc)
    scheduler = Scheduler(
        id="s", org_id="o", user_id="u", connection_id="c", provider="slack",
        frequency="weekly", prompt="p", status="active", last_run_at=now,
        next_run_at=now, attempts=0, last_error=None, created_at=now,
    )
    monkeypatch.setattr(
        runner, "get_user", lambda _: type("U", (), {"email": "a@b.c"})()
    )
    monkeypatch.setattr(
        runner, "fetch_activity",
        lambda *a, **k: ActivityDigest(items=(ActivityItem(summary="x"),), text="x"),
    )
    monkeypatch.setattr(
        runner.reports, "save_report", lambda **kw: type("R", (), {"id": "r"})()
    )
    monkeypatch.setattr(
        runner, "send_scheduler_report_email_safe", lambda *a, **k: False
    )

    # Deliberately hostile: a selection is live when the run starts.
    use_model(catalog.MODELS[0].id)
    runner.run_scheduler_once(scheduler, llm=_Probe())

    assert seen == [None], "a report must generate on the configured model"


# --------------------------------------------------------------------------
# Reasoning-token starvation (production incident)
# --------------------------------------------------------------------------
class _EmptyContentClient:
    """An endpoint that spends the whole token cap on internal reasoning.

    This is what a reasoning model actually returns: content is None,
    finish_reason is "length", and completion_tokens equals the cap. Note
    ``reasoning: {"exclude": True}`` does NOT prevent it — that strips
    reasoning from the RESPONSE while the tokens are still generated and still
    counted against max_tokens.
    """

    def __init__(self) -> None:
        self.chat = self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        cap = kwargs.get("max_tokens") or 0

        class _Msg:
            content = None
            tool_calls = None

        class _Choice:
            message = _Msg()
            finish_reason = "length"

        class _Usage:
            prompt_tokens = 2300
            completion_tokens = cap

        class _Resp:
            choices = [_Choice()]
            usage = _Usage()
            model = "some/reasoning-model:free"

        return _Resp()


def test_empty_content_error_names_the_model_and_the_cause():
    """The bare message "empty message content" cost a debugging session.

    It said nothing about WHICH of the selectable models failed or WHY, so the
    log was indistinguishable from a dead endpoint. The message must carry
    enough to diagnose it from the log alone.
    """
    from app.core.exceptions import LLMProviderError
    from app.llm.openai_provider import OpenAICompatProvider

    provider = OpenAICompatProvider(model="some/reasoning-model:free", api_key="k")
    provider._client = _EmptyContentClient()

    with pytest.raises(LLMProviderError) as exc:
        provider.generate("a grounded prompt", max_tokens=700)

    message = str(exc.value)
    assert "some/reasoning-model:free" in message, "must name the model"
    assert "finish_reason=length" in message, "must say why it was empty"
    assert "completion_tokens=700" in message, "must show the cap was consumed"
    assert "reasoning" in message.lower(), "must point at the actual cause"


def test_no_catalogued_model_is_a_known_reasoning_model():
    """Reasoning models cannot work under RAG_MAX_ANSWER_TOKENS=700.

    They return empty content, which is a hard LLMProviderError — chat shows an
    error instead of an answer. Verified in production, so these ids stay out
    until the admission test proves otherwise.
    """
    starved = {
        "dots-studio/dots-3-note-preview:free",
        "openrouter/free",  # routes to reasoning models at random
    }
    offered = {m.id for m in catalog.MODELS}
    assert not (offered & starved), (
        f"catalogued model(s) known to starve on the answer cap: {offered & starved}"
    )


# --------------------------------------------------------------------------
# Gemini for machinery, the member's pick for the answer
# --------------------------------------------------------------------------
def test_only_user_facing_stages_follow_the_selected_model():
    """A member's dropdown choice must not reach the grounding machinery.

    Query rewriting, the web-search decision and above all the groundedness
    AUDIT are what the product's guarantees rest on. An auditor running on
    whichever free model someone picked is worse than no auditor, because it
    still returns a verdict. Also keeps it to one OpenRouter call per question,
    which matters on a 50-request/day tier.
    """
    from app.llm.stages import AUX_LLM_STAGES, USER_FACING_LLM_STAGES
    from app.rag.pipeline import RagPipeline

    seen: dict[str, str | None] = {}

    class _Probe:
        model = "probe"
        last_usage = None

        def generate(self, prompt, **kwargs):
            seen[prompt] = selected_model()
            return "MODE: A\n\nok"

    pipeline = RagPipeline.__new__(RagPipeline)
    probe = _Probe()
    pipeline._llm = probe
    pipeline._llm_aux = probe

    picked = catalog.MODELS[0].id
    use_model(picked)

    every_stage = sorted(AUX_LLM_STAGES | USER_FACING_LLM_STAGES | {"web-decision"})
    for stage in every_stage:
        pipeline._generate_text(stage, prompt=stage)

    for stage in every_stage:
        expected = picked if stage in USER_FACING_LLM_STAGES else None
        assert seen[stage] == expected, (
            f"stage {stage!r} saw model {seen[stage]!r}, expected {expected!r}"
        )

    # The selection survives the internal stages — one must not strand the rest
    # of the request on the default.
    assert selected_model() == picked


def test_audit_never_runs_on_a_members_chosen_model():
    """Called out separately because it is the most expensive one to get wrong."""
    from app.llm.stages import STAGE_AUDIT, USER_FACING_LLM_STAGES

    assert STAGE_AUDIT not in USER_FACING_LLM_STAGES


# --------------------------------------------------------------------------
# Structural audit: no LLM call may bypass the split unnoticed
# --------------------------------------------------------------------------
def test_machinery_call_sites_pin_the_default_model():
    """These call the LLM directly, NOT through RagPipeline._generate_text.

    The stage split cannot reach them, so each has to pin the model itself.
    They are listed by name because the failure is silent: a member's pick
    would quietly start steering a decision rather than an answer, and nothing
    in the response would look wrong.
    """
    import inspect

    from app.agent.github_agent import GitHubAgent
    from app.rag.pipeline import RagPipeline

    machinery = [
        (RagPipeline, "_try_web_search"),
        (GitHubAgent, "_decide_tool"),
    ]
    for cls, name in machinery:
        dotted = f"{cls.__name__}.{name}"
        source = inspect.getsource(getattr(cls, name))
        assert "default_model_only" in source, (
            f"{dotted} calls the LLM outside _generate_text and does not pin "
            "the model — a member's choice would steer machinery"
        )


def test_ingestion_and_setup_chat_use_the_unrouted_aux_provider():
    """Neither may follow a member's pick, and both are guaranteed structurally.

    Ingest contextualization must write one corpus with one model; the setup
    chat is slot-filling, not prose.
    """
    import inspect

    import app.ingestion.pipeline as ingest
    import app.llm.factory as factory

    assert "RoutedLLMProvider" not in inspect.getsource(
        factory.build_aux_llm_provider
    ), "the aux provider must never be wrapped"
    assert "build_llm_provider" not in inspect.getsource(ingest), (
        "ingestion must use the aux (unrouted) provider only"
    )


# --------------------------------------------------------------------------
# Multiple backends (OpenRouter + Groq)
# --------------------------------------------------------------------------
def _routed(openrouter_key=None, groq_key=None):
    from app.config.settings import GroqSettings, OpenRouterSettings

    return RoutedLLMProvider(
        _Recording("default-model"),
        settings=OpenRouterSettings(api_key=openrouter_key),
        groq=GroqSettings(api_key=groq_key),
    )


def test_a_model_is_sent_to_its_own_backend():
    """The id alone cannot say where to send it — both hosts serve Llamas."""
    from app.llm.catalog import BACKEND_GROQ, GROQ_MODELS, MODELS

    provider = _routed(openrouter_key="or-key", groq_key="groq-key")

    use_model(MODELS[0].id)
    assert "openrouter.ai" in provider.active().base_url

    use_model(GROQ_MODELS[0].id)
    assert "groq.com" in provider.active().base_url
    assert GROQ_MODELS[0].backend == BACKEND_GROQ


def test_openrouter_routing_prefs_are_never_sent_to_groq():
    """``provider``/``reasoning`` are OpenRouter request extensions.

    Groq is a single provider on its own hardware: there is nothing to route
    between and no data policy to negotiate. Sending them is at best ignored
    and at worst a 400 on a stricter endpoint.
    """
    from app.llm.catalog import GROQ_MODELS, MODELS

    provider = _routed(openrouter_key="or-key", groq_key="groq-key")

    use_model(MODELS[0].id)
    assert provider.active()._extra_body is not None
    assert provider.active()._extra_body["provider"]["data_collection"] == "deny"

    use_model(GROQ_MODELS[0].id)
    assert provider.active()._extra_body is None


def test_a_backend_without_a_key_falls_back_rather_than_erroring():
    """Offering a model whose backend is unconfigured must not break chat."""
    from app.llm.catalog import GROQ_MODELS

    provider = _routed(openrouter_key="or-key", groq_key=None)
    use_model(GROQ_MODELS[0].id)
    assert provider.generate("hi") == "answered by default-model"


def test_picker_only_offers_models_whose_backend_is_configured():
    """A model that would silently answer on the default must not be listed.

    It would come back labelled with a model that never ran, which is worse
    than not offering it.
    """
    from app.llm import catalog as cat

    both = _routed("or-key", "groq-key").configured_backends()
    assert len(cat.as_dicts(both)) == len(cat.ALL_MODELS)

    or_only = _routed("or-key", None).configured_backends()
    assert all(d["backend"] == "openrouter" for d in cat.as_dicts(or_only))

    groq_only = _routed(None, "groq-key").configured_backends()
    assert all(d["backend"] == "groq" for d in cat.as_dicts(groq_only))

    assert cat.as_dicts(_routed(None, None).configured_backends()) == []
