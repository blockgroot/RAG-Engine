"""RAG_AUDIT_BACKEND=lettuce|jev — the remote answer checks (no network, no LLM).

Pins the contract it shares with the LLM audit: a flagged span over the
threshold downgrades, anything under it stands, and every way the endpoint can
fail SKIPS the audit instead of refusing a good answer.
"""

from __future__ import annotations

import httpx
import pytest

from app.config.settings import AuditSettings, RagSettings, RecoverySettings, ReuseSettings
from app.core.exceptions import ConfigurationError
from app.rag import audit
from app.rag.pipeline import RagPipeline
from .fakes import KeywordEmbedder, RecordingLLM, RecordingVectorStore

ORG = "org-audit-lettuce"
FALLBACK = "I don't have information on that in the available policy documents."
SETTINGS = AuditSettings(enabled=True, backend="lettuce", lettuce_url="https://checker.test/",
                         lettuce_token="hf_x", lettuce_threshold=0.6)


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def endpoint(monkeypatch):
    """Replace httpx.post; set ``endpoint.reply`` to a body, a status or an exception."""
    calls = []

    class E:
        reply = {"spans": []}

    def fake_post(url, json, headers, timeout):
        calls.append(dict(url=url, json=json, headers=headers))
        if isinstance(E.reply, BaseException):
            raise E.reply
        if isinstance(E.reply, int):
            return _Resp({}, status=E.reply)
        return _Resp(E.reply)

    monkeypatch.setattr(httpx, "post", fake_post)
    E.calls = calls
    return E


def _verdict(**kw):
    args = dict(question="How much leave?", contexts=["Leave is 25 days."], answer="You get 30 days.")
    args.update(kw)
    return audit.lettuce_verdict(SETTINGS, **args)


def test_span_over_threshold_is_ungrounded_and_names_the_span(endpoint):
    endpoint.reply = {"spans": [{"text": "30 days", "confidence": 0.91}, {"text": "x", "confidence": 0.2}]}
    v = _verdict()
    assert v.grounded is False
    assert "30 days" in v.reason
    call = endpoint.calls[0]
    assert call["url"] == "https://checker.test/check"
    assert call["headers"] == {"Authorization": "Bearer hf_x"}
    assert call["json"]["context"] == ["Leave is 25 days."]


@pytest.mark.parametrize("spans", [[], [{"text": "maybe", "confidence": 0.59}]])
def test_nothing_over_threshold_is_grounded(endpoint, spans):
    endpoint.reply = {"spans": spans}
    assert _verdict().grounded is True


@pytest.mark.parametrize("reply", [
    httpx.ConnectTimeout("sleeping space"), 503, ValueError("not json"), {"no_spans": []},
    {"spans": [{"confidence": "high"}]},
])
def test_every_endpoint_failure_skips_the_audit(endpoint, reply):
    endpoint.reply = reply
    assert _verdict() is None


def test_over_budget_context_is_not_audited_at_all(endpoint):
    # ModernBERT truncates past 8k tokens; auditing a cut context would flag
    # every claim sourced from the missing part as invented.
    big = ["x" * (audit.LETTUCE_MAX_CONTEXT_CHARS // 2 + 1)] * 2
    assert _verdict(contexts=big) is None
    assert endpoint.calls == []


def test_settings_reject_unknown_backend_and_missing_url(monkeypatch):
    monkeypatch.setenv("RAG_AUDIT_BACKEND", "laya")
    with pytest.raises(ConfigurationError):
        AuditSettings.from_env()
    monkeypatch.setenv("RAG_AUDIT_BACKEND", "lettuce")
    monkeypatch.delenv("RAG_AUDIT_LETTUCE_URL", raising=False)
    with pytest.raises(ConfigurationError):
        AuditSettings.from_env()


def test_settings_default_is_the_llm_backend(monkeypatch):
    for k in ("RAG_AUDIT_BACKEND", "RAG_AUDIT_LETTUCE_URL"):
        monkeypatch.delenv(k, raising=False)
    assert AuditSettings.from_env().backend == "llm"


def _pipeline(llm):
    return RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=RecordingVectorStore(ORG, content="leave: 25 days"),
        settings=RagSettings(top_k=3, similarity_threshold=0.1, fallback_response=FALLBACK),
        memory=None,
        web_search=None,
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
        audit_settings=SETTINGS,
    )


def test_pipeline_downgrades_on_a_flagged_answer_without_an_llm_audit_call(endpoint):
    endpoint.reply = {"spans": [{"text": "30 days", "confidence": 0.95}]}
    llm = RecordingLLM(answer="MODE: A\n\nEmployees get 30 days of paid annual leave.")
    result = _pipeline(llm).answer("How many leave days do I get?", org_id=ORG)
    assert result.audit_used is True
    assert result.audit_downgraded is True
    assert result.answer == FALLBACK
    assert llm.audit_calls == 0


def test_pipeline_keeps_the_answer_when_the_checker_is_down(endpoint):
    endpoint.reply = httpx.ConnectError("down")
    llm = RecordingLLM(answer="MODE: A\n\nEmployees get 25 days of paid annual leave.")
    result = _pipeline(llm).answer("How many leave days do I get?", org_id=ORG)
    assert result.answered is True
    assert result.audit_used is False
    assert result.answer == "Employees get 25 days of paid annual leave."


# -- Jev ------------------------------------------------------------------------

JEV = AuditSettings(enabled=True, backend="jev", jev_api_key="ts_x", jev_threshold=0.5)


def _jev(**kw):
    args = dict(question="How much leave?", contexts=["Leave is 25 days. Sick leave is 10 days."],
                answer="You get 25 days of leave. Sick leave is 30 days.")
    args.update(kw)
    return audit.jev_verdict(JEV, **args)


def test_jev_sends_one_request_with_a_question_per_sentence(endpoint):
    endpoint.reply = {"answers": {"s0": {"noul": 0.97}, "s1": {"noul": 0.08}}}
    v = _jev()
    assert v.grounded is False
    assert "Sick leave is 30 days." in v.reason  # the WEAKEST sentence is named
    (call,) = endpoint.calls
    assert call["url"] == audit.JEV_URL
    assert call["headers"] == {"Authorization": "Bearer ts_x"}
    body = call["json"]
    assert body["model"] == audit.JEV_MODEL
    assert set(body["questions"]) == {"s0", "s1"}
    assert all(q["type"] == "noul" for q in body["questions"].values())
    assert body["state"]["passages"] == ["Leave is 25 days. Sick leave is 10 days."]


def test_jev_every_sentence_supported_is_grounded(endpoint):
    endpoint.reply = {"answers": {"s0": {"noul": 0.97}, "s1": {"noul": 0.61}}}
    assert _jev().grounded is True


@pytest.mark.parametrize("reply", [
    httpx.ReadTimeout("slow"), 401, 429, 529, {"answers": {"s0": {"noul": 0.9}}},  # s1 missing
    {"answers": {"s0": {"noul": "yes"}, "s1": {"noul": 0.9}}},
])
def test_jev_every_failure_skips_the_audit(endpoint, reply):
    endpoint.reply = reply
    assert _jev() is None


def test_jev_over_budget_context_is_not_sent(endpoint):
    assert _jev(contexts=["x" * (audit.JEV_MAX_CONTEXT_CHARS + 1)]) is None
    assert endpoint.calls == []


def test_long_answers_merge_the_tail_instead_of_dropping_it():
    text = " ".join(f"Claim number {i}." for i in range(30))
    parts = audit.split_sentences(text)
    assert len(parts) == audit.JEV_MAX_SENTENCES
    assert "Claim number 29." in parts[-1]


def test_jev_backend_needs_a_key(monkeypatch):
    monkeypatch.setenv("RAG_AUDIT_BACKEND", "jev")
    monkeypatch.delenv("RAG_AUDIT_JEV_API_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        AuditSettings.from_env()
