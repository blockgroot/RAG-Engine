"""The company's key offers every chat model on the key's own list.

One request lists the models and proves the key; nothing is test-answered per
model. A model that cannot chart or call tools says so when it is used.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import llm_model
from app.core.exceptions import ConfigurationError, LLMProviderError
from app.llm import org_model
from app.llm.org_model import OrgModel, get_preset


class _Reply:
    def __init__(self, status: int, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def _serve(monkeypatch, status=200, body=None):
    seen = {}

    def get(url, headers, timeout):
        seen.update(url=url, headers=headers)
        return _Reply(status, body)

    import httpx

    monkeypatch.setattr(httpx, "get", get)
    return seen


def test_a_keys_chat_models_are_listed_without_speech_or_embeddings(monkeypatch):
    seen = _serve(monkeypatch, body={"data": [{"id": "gpt-5"}, {"id": "gpt-5-mini"},
                                               {"id": "text-embedding-3-large"}, {"id": "whisper-1"},
                                               {"id": "gpt-4o-mini-tts"}, {"id": "gpt-image-1"},
                                               {"id": "davinci-002"}, {"id": "gpt-5"}]})
    assert org_model.list_provider_models(get_preset("openai"), "sk-1") == ["gpt-5", "gpt-5-mini"]
    assert seen["url"] == "https://api.openai.com/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer sk-1"


def test_gemini_ids_lose_their_models_prefix(monkeypatch):
    _serve(monkeypatch, body={"data": [{"id": "models/gemini-3.1-pro"}]})
    assert org_model.list_provider_models(get_preset("google"), "k") == ["gemini-3.1-pro"]


def test_anthropic_is_listed_on_its_own_api(monkeypatch):
    seen = _serve(monkeypatch, body={"data": [{"id": "claude-haiku-5-5"}]})
    assert org_model.list_provider_models(get_preset("anthropic"), "sk-ant") == ["claude-haiku-5-5"]
    assert seen["url"].startswith("https://api.anthropic.com/v1/models")
    assert seen["headers"]["x-api-key"] == "sk-ant"


def test_a_refused_key_and_a_missing_list_are_told_apart(monkeypatch):
    _serve(monkeypatch, status=401, body={})
    with pytest.raises(ConfigurationError, match="refused this API key") as refused:
        org_model.list_provider_models(get_preset("openai"), "bad")
    assert not isinstance(refused.value, org_model.ModelListUnavailable)
    _serve(monkeypatch, status=404, body={})
    with pytest.raises(org_model.ModelListUnavailable):
        org_model.list_provider_models(get_preset("perplexity"), "k")


def _listed(monkeypatch, models):
    monkeypatch.setattr(org_model, "list_provider_models", lambda preset, key: models)


def test_every_chat_model_the_key_lists_is_saved_without_picking(monkeypatch):
    _listed(monkeypatch, ["gpt-5", "gpt-5-mini"])
    preset, key, models = llm_model.checked_models({"preset": "openai", "api_key": "sk-1"}, "org")
    assert (preset.id, key, models) == ("openai", "sk-1", ["gpt-5", "gpt-5-mini"])
    _listed(monkeypatch, [])
    with pytest.raises(HTTPException, match="no chat models"):
        llm_model.checked_models({"preset": "openai", "api_key": "sk-1"}, "org")


def test_a_blank_key_reuses_the_saved_one_only_on_the_same_provider(monkeypatch):
    _listed(monkeypatch, ["gpt-5"])
    saved = OrgModel(model="gpt-5", api_key="sk-saved", base_url="", preset="openai", label="", version="1")
    monkeypatch.setattr(org_model, "get_org_model", lambda org: saved)
    _, key, _ = llm_model.checked_models({"preset": "openai"}, "org")
    assert key == "sk-saved"
    with pytest.raises(HTTPException, match="Paste the API key"):
        llm_model.checked_models({"preset": "groq"}, "org")


def test_a_provider_without_a_list_tests_each_typed_model_at_once(monkeypatch):
    def unavailable(preset, key):
        raise org_model.ModelListUnavailable("no list")

    monkeypatch.setattr(org_model, "list_provider_models", unavailable)
    probed = []
    monkeypatch.setattr(llm_model, "_probe", lambda preset, model, key: probed.append(model))
    assert llm_model.checked_models({"preset": "perplexity", "api_key": "k"}, "org") == "no list"
    _, _, models = llm_model.checked_models(
        {"preset": "perplexity", "api_key": "k", "models": ["sonar", "sonar-pro"]}, "org")
    assert models == ["sonar", "sonar-pro"] and sorted(probed) == ["sonar", "sonar-pro"]


def test_every_offered_model_routes_on_the_company_key_and_no_other(monkeypatch):
    from app.llm import routed

    org = OrgModel(model="gpt-5", api_key="sk", base_url="https://api.openai.com/v1", preset="openai",
                   label="", version="1", models=("gpt-5", "gpt-5-mini"))
    monkeypatch.setattr(org_model, "get_org_model", lambda org_id: org)
    provider = routed.RoutedLLMProvider.__new__(routed.RoutedLLMProvider)
    provider._clients, provider._custom_timeout = {}, 30.0
    client = provider._org_client("o1", "gpt-5-mini")
    assert client.model == "gpt-5-mini" and client.billing == "company"
    with pytest.raises(ConfigurationError):
        provider._org_client("o1", "gpt-6")


def test_an_old_row_offers_its_one_model():
    assert OrgModel(model="gpt-5", api_key="", base_url="", preset="openai", label="", version="1").models == ("gpt-5",)


def test_a_model_that_cannot_do_this_mode_says_pick_another():
    from app.api import actions, chat

    tools = LLMProviderError("Error code: 400 - tools are not supported for this model")
    assert "Pick another model" in chat._user_facing_llm_error(tools)
    assert actions._model_down(tools) == actions._NO_TOOLS
    assert "couldn't reach" in chat._user_facing_llm_error(LLMProviderError("Error code: 500 - boom"))
