"""Post-generation groundedness audit — the validation-layer gap.

Deterministic unit tests with fakes (no DB/real LLM), mirroring the style of
test_recovery.py. Proves: off by default (byte-identical behaviour), a
GROUNDED verdict leaves the answer untouched, an UNGROUNDED verdict downgrades
to the fixed fallback, and an audit failure degrades gracefully (answer
stands, exactly like a failed recovery expansion).
"""

from __future__ import annotations

from app.config.settings import AuditSettings, RagSettings, RecoverySettings, ReuseSettings
from app.rag.audit import parse_audit_verdict
from app.rag.pipeline import RagPipeline
from .fakes import KeywordEmbedder, RecordingLLM, RecordingVectorStore

ORG = "org-audit"
FALLBACK = "I don't have information on that in the available policy documents."
TAGGED_ANSWER = "MODE: A\n\nEmployees get 25 days of paid annual leave."


def _pipeline(llm: RecordingLLM, *, audit_enabled: bool) -> RagPipeline:
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
        audit_settings=AuditSettings(enabled=audit_enabled),
    )


def test_audit_off_by_default_leaves_answer_untouched():
    llm = RecordingLLM(answer=TAGGED_ANSWER)
    pipe = _pipeline(llm, audit_enabled=False)

    result = pipe.answer("How many leave days do I get?", org_id=ORG)

    assert result.answered is True
    assert result.answer == "Employees get 25 days of paid annual leave."
    assert result.audit_used is False
    assert result.audit_downgraded is False
    assert llm.audit_calls == 0


def test_grounded_verdict_leaves_answer_unchanged():
    llm = RecordingLLM(answer=TAGGED_ANSWER, audit_verdict="VERDICT: GROUNDED\nREASON: (none)")
    pipe = _pipeline(llm, audit_enabled=True)

    result = pipe.answer("How many leave days do I get?", org_id=ORG)

    assert result.answered is True
    assert result.answer == "Employees get 25 days of paid annual leave."
    assert result.audit_used is True
    assert result.audit_downgraded is False
    assert llm.audit_calls == 1


def test_ungrounded_verdict_downgrades_to_fallback():
    llm = RecordingLLM(
        answer=TAGGED_ANSWER,
        audit_verdict="VERDICT: UNGROUNDED\nREASON: 25 days is not stated in context",
    )
    pipe = _pipeline(llm, audit_enabled=True)

    result = pipe.answer("How many leave days do I get?", org_id=ORG)

    assert result.answered is False
    assert result.answer == FALLBACK
    assert result.source == "none"
    assert result.audit_used is True
    assert result.audit_downgraded is True
    assert result.audit_reason == "25 days is not stated in context"


def test_unparseable_verdict_leaves_answer_unchanged():
    """An aux model returning junk must never be read as evidence of ungroundedness."""
    llm = RecordingLLM(answer=TAGGED_ANSWER, audit_verdict="I cannot help with that.")
    pipe = _pipeline(llm, audit_enabled=True)

    result = pipe.answer("How many leave days do I get?", org_id=ORG)

    assert result.answered is True
    assert result.answer == "Employees get 25 days of paid annual leave."
    assert result.audit_used is False
    assert result.audit_downgraded is False


def test_audit_llm_failure_degrades_gracefully():
    """Same philosophy as recovery's expander failure: never block the answer."""

    class _RaisingLLM(RecordingLLM):
        def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
            if "DRAFT ANSWER:" in prompt:
                raise RuntimeError("simulated audit failure")
            return super().generate(prompt, max_tokens=max_tokens)

    llm = _RaisingLLM(answer=TAGGED_ANSWER)
    pipe = _pipeline(llm, audit_enabled=True)

    result = pipe.answer("How many leave days do I get?", org_id=ORG)

    assert result.answered is True
    assert result.answer == "Employees get 25 days of paid annual leave."
    assert result.audit_used is False
    assert result.audit_downgraded is False


def test_mode_c_refusal_is_never_audited():
    """Mode C is already a refusal — nothing to fact-check, and no extra call."""
    llm = RecordingLLM(answer=f"MODE: C\n\n{FALLBACK}")
    pipe = _pipeline(llm, audit_enabled=True)

    result = pipe.answer("How many leave days do I get?", org_id=ORG)

    assert result.answered is False
    assert llm.audit_calls == 0


def test_parse_audit_verdict_grounded():
    v = parse_audit_verdict("VERDICT: GROUNDED\nREASON: (none)")
    assert v.grounded is True
    assert v.reason is None


def test_parse_audit_verdict_ungrounded_with_reason():
    v = parse_audit_verdict("VERDICT: UNGROUNDED\nREASON: invents a dollar amount")
    assert v.grounded is False
    assert v.reason == "invents a dollar amount"


def test_parse_audit_verdict_unparseable_is_none():
    v = parse_audit_verdict("I'm not sure what you mean.")
    assert v.grounded is None
    assert v.reason is None


def test_parse_audit_verdict_case_insensitive():
    v = parse_audit_verdict("verdict: grounded\nreason: fine")
    assert v.grounded is True
