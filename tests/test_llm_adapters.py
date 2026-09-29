"""``LLM_ADAPTER``: the provider is named from a fixed list, never implied.

Pinned: an unknown adapter or a conflicting base URL refuses to build; a named
adapter supplies its own URL; no adapter keeps today's behaviour byte for byte;
the boot check fails only on PROOF (a listed-and-absent model), never on an
unreachable list or a legacy config.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config.settings import LLMSettings
from app.core.exceptions import ConfigurationError
from app.llm import adapters
from app.llm.factory import aux_endpoint, build_aux_llm_provider, build_llm_provider, main_endpoint

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _settings(**kw) -> LLMSettings:
    base = dict(model="m", aux_model=None, api_key="k", base_url=None)
    base.update(kw)
    return LLMSettings(**base)


class _Models:
    def __init__(self, ids=None, error=None):
        self._ids, self._error = ids or [], error

    @property
    def models(self):
        return self

    def list(self):
        if self._error:
            raise self._error
        return [SimpleNamespace(id=i) for i in self._ids]


# -- resolution --------------------------------------------------------------------


def test_a_named_adapter_supplies_its_own_url():
    ep = main_endpoint(_settings(adapter="groq"))
    assert (ep.adapter, ep.base_url) == ("groq", "https://api.groq.com/openai/v1")


def test_aliases_resolve():
    assert main_endpoint(_settings(adapter="gemini")).base_url == GEMINI_URL


def test_an_unknown_adapter_refuses_to_build():
    with pytest.raises(ConfigurationError, match="Unknown LLM adapter 'gemni'"):
        build_llm_provider(_settings(adapter="gemni"))


def test_a_conflicting_base_url_is_an_error_not_an_override():
    with pytest.raises(ConfigurationError, match="conflicts with LLM_ADAPTER=groq"):
        main_endpoint(_settings(adapter="groq", base_url=GEMINI_URL))
    # The same URL, trailing slash aside, is not a conflict.
    assert main_endpoint(_settings(adapter="google", base_url=GEMINI_URL.rstrip("/"))).adapter == "google"


def test_custom_needs_a_base_url():
    with pytest.raises(ConfigurationError, match="needs LLM_BASE_URL"):
        main_endpoint(_settings(adapter="custom"))
    ep = main_endpoint(_settings(adapter="custom", base_url="http://localhost:3001/v1"))
    assert ep.base_url == "http://localhost:3001/v1"


def test_no_adapter_is_exactly_the_legacy_behaviour():
    ep = main_endpoint(_settings(base_url=GEMINI_URL))
    assert (ep.adapter, ep.base_url, ep.model) == (None, GEMINI_URL, "m")
    llm = build_llm_provider(_settings(base_url=GEMINI_URL))
    assert llm._default.base_url == GEMINI_URL


def test_every_preset_is_an_adapter_with_a_fixed_url():
    from app.llm.org_model import PRESETS

    for preset in PRESETS:
        assert adapters.get_adapter(preset.id).base_url == preset.base_url


# -- aux ----------------------------------------------------------------------------


def test_aux_shares_the_main_adapter_by_default():
    ep = aux_endpoint(_settings(adapter="groq", aux_model="small"))
    assert (ep.base_url, ep.model, ep.api_key) == ("https://api.groq.com/openai/v1", "small", "k")


def test_aux_adapter_plus_key_is_its_own_endpoint():
    s = _settings(adapter="google", aux_adapter="groq", aux_api_key="aux-key")
    assert s.aux_has_own_endpoint
    llm = build_aux_llm_provider(s)
    assert (llm.base_url, llm.api_key) == ("https://api.groq.com/openai/v1", "aux-key")


def test_aux_adapter_without_its_key_is_not_configured():
    s = _settings(adapter="google", aux_adapter="groq")
    assert not s.aux_has_own_endpoint
    assert aux_endpoint(s).base_url == GEMINI_URL


# -- settings -----------------------------------------------------------------------


def test_env_is_read_and_normalised(monkeypatch):
    monkeypatch.setenv("LLM_ADAPTER", " Groq ")
    monkeypatch.setenv("LLM_CONFIG_CHECK", "nonsense")
    s = LLMSettings.from_env()
    assert s.adapter == "groq"
    assert s.config_check == "strict"  # an unknown mode never disables the check


# -- model check ----------------------------------------------------------------------


def _ep(model="gemini-3.5-flash", adapter="google"):
    return adapters.Endpoint(adapter=adapter, base_url=GEMINI_URL, model=model, api_key="k")


def test_a_listed_model_is_ok_including_the_models_prefix():
    check = adapters.verify_model(_ep(), client=_Models(["models/gemini-3.5-flash"]))
    assert check.status == adapters.OK


def test_an_absent_model_is_not_found_with_suggestions():
    check = adapters.verify_model(_ep("gemini-3.5-flsh"), client=_Models(["models/gemini-3.5-flash"]))
    assert check.status == adapters.NOT_FOUND
    assert "gemini-3.5-flash" in check.detail


def test_an_unreachable_list_is_unverified_never_a_failure():
    check = adapters.verify_model(_ep(), client=_Models(error=ConnectionError("down")))
    assert check.status == adapters.UNVERIFIED
    assert adapters.verify_model(_ep(), client=_Models([])).status == adapters.UNVERIFIED


# -- the boot script ---------------------------------------------------------------------


@pytest.fixture
def boot():
    path = Path(__file__).resolve().parent.parent / "scripts" / "check_llm_config.py"
    spec = importlib.util.spec_from_file_location("check_llm_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_boot_check_fails_on_a_wrong_model_for_a_named_adapter(boot):
    problems = boot.check(
        _settings(adapter="google", model="gpt-4o"),
        client_for=lambda ep: _Models(["models/gemini-3.5-flash"]),
    )
    assert problems and "gpt-4o" in problems[0]


def test_boot_check_fails_on_an_unknown_adapter(boot):
    assert "Unknown LLM adapter" in boot.check(_settings(adapter="nope"))[0]


def test_boot_check_never_blocks_a_legacy_config(boot):
    problems = boot.check(
        _settings(base_url=GEMINI_URL, model="gpt-4o"),
        client_for=lambda ep: _Models(["models/gemini-3.5-flash"]),
    )
    assert problems == []


def test_boot_check_never_blocks_on_an_unreachable_provider(boot):
    problems = boot.check(
        _settings(adapter="google"), client_for=lambda ep: _Models(error=TimeoutError())
    )
    assert problems == []


def test_boot_check_verifies_a_separate_aux_model(boot):
    problems = boot.check(
        _settings(adapter="google", model="gemini-3.5-flash", aux_model="tiny"),
        client_for=lambda ep: _Models(["models/gemini-3.5-flash"]),
    )
    assert problems == ["LLM_AUX: " + adapters.verify_model(
        adapters.Endpoint("google", GEMINI_URL, "tiny", "k"),
        client=_Models(["models/gemini-3.5-flash"]),
    ).detail]


def test_a_rejected_key_is_proof_but_only_as_a_401():
    class Denied(Exception):
        status_code = 401

    class Bad(Exception):
        status_code = 400

    assert adapters.verify_model(_ep(), client=_Models(error=Denied())).status == adapters.BAD_KEY
    assert adapters.verify_model(_ep(), client=_Models(error=Bad())).status == adapters.UNVERIFIED


def test_boot_check_fails_on_a_rejected_key_for_a_named_adapter(boot):
    class Denied(Exception):
        status_code = 401

    problems = boot.check(_settings(adapter="openai"), client_for=lambda ep: _Models(error=Denied()))
    assert problems and "rejected the API key" in problems[0]
