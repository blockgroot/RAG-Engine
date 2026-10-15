"""Our per-vendor request extras (thinking or reasoning off) adjust to what each
model accepts, learned from its own 400, with no model or vendor named in code."""

from __future__ import annotations

import httpx
import pytest
from openai import BadRequestError

from app.llm import openai_provider
from app.llm.openai_provider import OpenAICompatProvider

OURS = {"thinking": {"type": "disabled"}}


def _bad(message):
    request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
    return BadRequestError(message, response=httpx.Response(400, request=request), body=None)


class FakeCompletions:
    """Refuses requests for which ``refuse(extra_body)`` returns a message."""

    def __init__(self, refuse):
        self.refuse, self.sent = refuse, []

    def create(self, **kwargs):
        extra = kwargs.get("extra_body")
        self.sent.append(extra)
        message = self.refuse(extra)
        if message:
            raise _bad(message)
        return "ok"


def _provider(refuse, *, model="any-model", optional=True, extras=OURS):
    p = OpenAICompatProvider(model=model, api_key="k", base_url="https://llm.example/v1",
                             extra_body=extras, extra_body_optional=optional)
    fake = FakeCompletions(refuse)
    p._client = type("C", (), {"chat": type("Ch", (), {"completions": fake})()})()
    return p, fake


@pytest.fixture(autouse=True)
def _forget():
    openai_provider._LEARNED_EXTRAS.clear()
    yield
    openai_provider._LEARNED_EXTRAS.clear()


def test_the_value_the_model_names_is_used_and_remembered_across_clients():
    def refuse(extra):
        if (extra or {}).get("thinking") == {"type": "disabled"}:
            return 'To turn thinking off on this model, send "thinking": {"type": "between_tools"} instead.'
    p, fake = _provider(refuse)
    assert p._create({"model": "m"}) == "ok"
    assert fake.sent == [OURS, {"thinking": {"type": "between_tools"}}]
    q, fake2 = _provider(refuse)                       # a new client, as Act builds per request
    assert q._create({"model": "m"}) == "ok" and fake2.sent == [{"thinking": {"type": "between_tools"}}]


def test_a_field_the_model_rejects_without_an_alternative_is_dropped_alone():
    extras = {"thinking": {"type": "disabled"}, "reasoning_effort": "minimal"}

    def refuse(extra):
        if "reasoning_effort" in (extra or {}):
            return "Unrecognized request argument supplied: reasoning_effort"
    p, fake = _provider(refuse, extras=extras)
    assert p._create({"model": "m"}) == "ok"
    assert fake.sent[-1] == {"thinking": {"type": "disabled"}}


def test_a_model_that_takes_our_fields_keeps_them():
    p, fake = _provider(lambda extra: None)
    assert p._create({"model": "m"}) == "ok" and fake.sent == [OURS]
    assert openai_provider._LEARNED_EXTRAS == {}


def test_a_400_not_about_our_fields_keeps_them():
    """A prompt too long fails both ways; the thinking setting must survive it."""
    p, fake = _provider(lambda extra: "prompt is too long: 250000 tokens")
    with pytest.raises(BadRequestError):
        p._create({"model": "m"})
    assert p._extra_body == OURS and openai_provider._LEARNED_EXTRAS == {}


def test_required_extras_are_never_changed():
    """OpenRouter's data_collection: deny must fail rather than be dropped."""
    extras = {"provider": {"data_collection": "deny"}}
    p, fake = _provider(lambda extra: "provider is not supported", optional=False, extras=extras)
    with pytest.raises(BadRequestError):
        p._create({"model": "m"})
    assert fake.sent == [extras]
