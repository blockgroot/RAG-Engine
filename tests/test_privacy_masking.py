"""Identifier masking before text leaves for an outside service (app/privacy).

No network: every outbound client is faked and the test reads what it was sent.
"""

from __future__ import annotations

import httpx
import pytest
from stdnum import verhoeff

from app.config.settings import LLMSettings, PrivacySettings
from app.core.exceptions import LLMProviderError, PrivacyError
from app.llm.base import ChatResult, LLMProvider, ToolCall
from app.llm.masking import PLACEHOLDER_NOTE, MaskingLLMProvider
from app.privacy import IdentifierDetector, PatternDetector, Vault, build_detector


def _with_check(digits: str) -> str:
    return digits + verhoeff.calc_check_digit(digits)


AADHAAR = _with_check("23456789012")            # 12 digits, valid Verhoeff
VID = _with_check("234567890123456")            # 16 digits, valid Verhoeff
BAD_AADHAAR = AADHAAR[:-1] + str((int(AADHAAR[-1]) + 1) % 10)
PAN = "ABCPE1234F"                              # 4th letter P: a person
CARD = "4111 1111 1111 1111"                    # Luhn-valid test card
MOBILE = "+91 98765 43210"

SAMPLE = (
    f"Aadhaar {AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]}, VID {VID}, PAN {PAN}, "
    f"card {CARD}, mobile {MOBILE}, IFSC HDFC0001234, UPI sana@okaxis."
)
SECRETS = [AADHAAR[:4], VID, PAN, "4111", "98765", "HDFC0001234", "sana@okaxis"]


def _kinds(text: str) -> list[str]:
    return [f.kind for f in PatternDetector().find(text)]


# -- detection ----------------------------------------------------------------------


def test_every_kind_is_found():
    assert _kinds(SAMPLE) == ["AADHAAR", "VID", "PAN", "CARD", "PHONE", "IFSC", "UPI"]


@pytest.mark.parametrize("text", [
    f"id {BAD_AADHAAR}",                 # 12 digits, wrong check digit
    "PAN ABCXE1234F",                    # X is not a PAN holder type
    "card 4111 1111 1111 1112",          # fails Luhn
    "order 5012345678",                  # order id: not a mobile
    "amount ₹1,25,000 paid",
    "ts 1712345678.123456",              # Slack timestamp / decimal
    "office 022 2345 6789",              # landline without +: left alone
    "date 12/10/2026, year 2024",
    "mail sana@syvora.com",              # email, not a UPI handle
    "sha 4a7d1ed414474e4033ac29ccb8653d9b",
])
def test_lookalikes_are_left_alone(text):
    assert _kinds(text) == []


@pytest.mark.parametrize("phone", ["9876543210", "098765-43210", "+91-98765-43210", "+44 7911 123456"])
def test_mobile_spellings(phone):
    assert _kinds(f"call {phone} now") == ["PHONE"]


# -- vault --------------------------------------------------------------------------


def test_mask_hides_every_value_and_restore_brings_them_back():
    vault = Vault(PatternDetector())
    masked = vault.mask(SAMPLE)
    assert not any(s in masked for s in SECRETS)
    assert vault.restore(masked) == SAMPLE


def test_one_number_two_spellings_share_a_placeholder():
    vault = Vault(PatternDetector())
    masked = vault.mask("Call 9876543210. Doc says +91 98765 43210.")
    assert masked == "Call <PHONE_1>. Doc says <PHONE_1>."
    # Restored as first written: the same number either way.
    assert vault.restore("Ring <PHONE_1>.") == "Ring 9876543210."


def test_restore_accepts_bare_form_and_drops_invented_placeholders():
    vault = Vault(PatternDetector())
    vault.mask(f"PAN {PAN}")
    assert vault.restore("It is PAN_1, not <PAN_2>.") == f"It is {PAN}, not ."


def test_off_switch_is_a_pass_through(monkeypatch):
    monkeypatch.setenv("MASK_IDENTIFIERS", "false")
    assert build_detector() is None
    vault = Vault(None)
    assert vault.mask(SAMPLE) == SAMPLE
    assert vault.restore("<PHONE_1>") == "<PHONE_1>"
    assert build_detector(PrivacySettings(mask_identifiers=True)) is not None


class _Broken(IdentifierDetector):
    def find(self, text):
        raise RuntimeError("boom")


def test_detector_failure_blocks_the_send():
    with pytest.raises(PrivacyError):
        Vault(_Broken()).mask("anything")


# -- the LLM wrapper ----------------------------------------------------------------


class _Recorder(LLMProvider):
    model = "fake-model"

    def __init__(self, reply="", tool_args="{}"):
        self.prompts: list = []
        self.reply, self.tool_args = reply, tool_args

    def generate(self, prompt, *, max_tokens=None):
        self.prompts.append(prompt)
        return self.reply

    def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
        self.prompts.append(messages)
        call = ToolCall(id="c1", name="lookup", arguments=self.tool_args)
        return ChatResult(text=self.reply, tool_calls=[call],
                          raw_message={"tool_calls": [{"function": {"arguments": self.tool_args}}]})


def test_wrapper_masks_the_prompt_and_restores_the_reply():
    inner = _Recorder(reply="Their mobile is <PHONE_1>.")
    llm = MaskingLLMProvider(inner, PatternDetector())
    out = llm.generate(SAMPLE)
    sent = inner.prompts[0]
    assert sent.startswith(PLACEHOLDER_NOTE)
    assert not any(s in sent for s in SECRETS)
    assert out == f"Their mobile is {MOBILE}."


def test_wrapper_leaves_a_plain_prompt_untouched():
    inner = _Recorder(reply="ok")
    MaskingLLMProvider(inner, PatternDetector()).generate("What is our leave policy?")
    assert inner.prompts == ["What is our leave policy?"]


def test_tool_calls_get_real_values_and_replayed_arguments_are_masked():
    inner = _Recorder(tool_args='{"pan": "<PAN_1>"}')
    llm = MaskingLLMProvider(inner, PatternDetector())
    messages = [
        {"role": "user", "content": f"Look up PAN {PAN}"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"function": {"name": "lookup", "arguments": f'{{"pan": "{PAN}"}}'}}]},
    ]
    result = llm.generate_with_tools(messages)
    sent = repr(inner.prompts[0])
    assert PAN not in sent
    assert result.tool_calls[0].arguments == f'{{"pan": "{PAN}"}}'
    assert result.raw_message["tool_calls"][0]["function"]["arguments"] == f'{{"pan": "{PAN}"}}'
    assert messages[0]["content"] == f"Look up PAN {PAN}"  # caller's list not mutated


def test_wrapper_fails_closed_as_an_llm_error():
    inner = _Recorder()
    llm = MaskingLLMProvider(inner, _Broken())
    with pytest.raises(LLMProviderError):
        llm.generate("text")
    with pytest.raises(LLMProviderError):
        llm.generate_with_tools([{"role": "user", "content": "text"}])
    assert inner.prompts == []


def test_wrapper_passes_attributes_through():
    assert MaskingLLMProvider(_Recorder(), None).model == "fake-model"


def test_both_factory_providers_are_masked():
    from app.llm.factory import build_aux_llm_provider, build_llm_provider

    s = LLMSettings(model="m", aux_model=None, api_key="k", base_url="https://example.com/v1")
    assert isinstance(build_llm_provider(s), MaskingLLMProvider)
    assert isinstance(build_aux_llm_provider(s), MaskingLLMProvider)


# -- search and the guard ------------------------------------------------------------


def test_remote_embedder_sends_masked_text():
    from app.embeddings.remote import RemoteEmbeddingProvider

    sent: list = []

    class _Emb:
        def create(self, model, input):  # noqa: A002
            sent.extend(input)
            item = type("I", (), {"index": 0, "embedding": [0.0]})
            return type("R", (), {"data": [item]})

    provider = RemoteEmbeddingProvider(model="m", api_key="k", base_url="https://example.com/v1")
    provider._client = type("C", (), {"embeddings": _Emb()})()
    provider.embed([SAMPLE])
    assert not any(s in sent[0] for s in SECRETS)


def test_remote_reranker_sends_masked_text(monkeypatch):
    from app.reranker.remote import RemoteReranker
    from app.vectorstore.base import RetrievedChunk

    captured: dict = {}

    def fake_post(self, path, json=None):  # noqa: A002
        captured.update(json)
        return httpx.Response(200, json={"results": []}, request=httpx.Request("POST", "https://x"))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    chunk = RetrievedChunk(content=SAMPLE, score=0.5, document_id="d", chunk_index=0, org_id="o")
    RemoteReranker(model="m", api_key="k").rerank(f"who has PAN {PAN}?", [chunk], top_k=1)
    assert PAN not in captured["query"]
    assert not any(s in captured["documents"][0] for s in SECRETS)
    assert "<PAN_1>" in captured["query"] and "<PAN_1>" in captured["documents"][0]


def test_groq_checks_send_masked_text(monkeypatch):
    from app.guard.safeguard import groq_label

    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        captured.update(json)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert groq_label(api_key="k", base_url="https://x", system="s", text=SAMPLE, timeout=1) == "OK"
    assert not any(s in captured["messages"][1]["content"] for s in SECRETS)
