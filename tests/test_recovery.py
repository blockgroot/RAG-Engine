"""Bounded retrieval recovery tests (Retrieval Discovery Gap).

Deterministic unit tests with fakes — no DB, real LLM, or embedding model.
They prove the recovery control flow against the nine generic categories from
the design: vocabulary mismatch, synonym/abbreviation, typo, broad exploratory,
unsupported inference prevention, related-but-not-explicit, trigger validation,
bound enforcement, and graceful degradation.
"""

from __future__ import annotations

from app.config.settings import RagSettings, RecoverySettings, ReuseSettings
from app.rag.pipeline import (
    RECOVERY_REASON_GATE_MISS,
    RECOVERY_REASON_INSUFFICIENT_EVIDENCE,
    RagPipeline,
)
from app.rag.prompts import build_grounded_prompt
from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

ORG = "org-recovery"
FALLBACK = "I don't have information on that in the available policy documents."
TARGET_CHUNK = "leave wellness allowance covers health-related products and supplements"


def _pipeline(
    llm: RecordingLLM,
    store: TopicAwareVectorStore,
    *,
    recovery: RecoverySettings | None = None,
    top_k: int = 3,
) -> RagPipeline:
    return RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(
            top_k=top_k, similarity_threshold=0.35, fallback_response=FALLBACK
        ),
        memory=None,
        web_search=None,
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=recovery or RecoverySettings(enabled=True, max_queries=2),
    )


def test_vocabulary_mismatch_recovery_finds_chunk():
    """Retrieval Discovery Gap: user wording misses; recovery expression hits."""
    store = TopicAwareVectorStore(ORG, chunks=[("doc-1", TARGET_CHUNK)])
    llm = RecordingLLM(
        recovery_queries=["leave wellness allowance health products"],
        answer="Wellness allowance covers health-related products. [1]",
    )
    pipe = _pipeline(llm, store)

    # First query has no topic keywords → gate miss → recovery → leave keyword hits.
    result = pipe.answer("Can I get protein supplements reimbursed?", org_id=ORG)

    assert result.recovery_used is True
    assert result.recovery_reason == RECOVERY_REASON_GATE_MISS
    assert result.recovery_queries
    assert result.answered is True
    assert result.source == "policy"
    assert "wellness" in result.answer.lower() or "allowance" in result.answer.lower()
    assert result.retrieval_improved is True
    # First pass may return no hits (top_score_before=None); after recovery we have a score.
    assert result.top_score_after is not None
    before = result.top_score_before if result.top_score_before is not None else 0.0
    assert result.top_score_after > before
    assert result.final_answer_source == "policy"
    assert result.latency_ms is not None


def test_synonym_abbreviation_handling():
    """Recovery expressions with document terminology retrieve the right chunk."""
    store = TopicAwareVectorStore(
        ORG, chunks=[("doc-1", "dental coverage: annual cleaning included")]
    )
    llm = RecordingLLM(
        recovery_queries=["dental coverage annual cleaning"],
        answer="Annual cleaning is included in dental coverage. [1]",
    )
    pipe = _pipeline(llm, store)

    result = pipe.answer("Is teeth cleaning covered under oral benefits?", org_id=ORG)

    assert result.recovery_used is True
    assert any("dental" in q.lower() for q in result.recovery_queries)
    assert result.answered is True
    assert "dental" in " ".join(s.content for s in result.sources).lower()


def test_typo_recovery_preserves_original_question_for_generation():
    """Typo in the user question: recovery may correct for retrieval; generate keeps original."""
    store = TopicAwareVectorStore(ORG, chunks=[("doc-1", "leave: 25 days paid annual")])
    llm = RecordingLLM(
        recovery_queries=["leave paid annual days"],
        answer="Full-time staff get 25 days. [1]",
    )
    pipe = _pipeline(llm, store)

    original = "How many anuual leav days do we get?"
    result = pipe.answer(original, org_id=ORG)

    assert result.recovery_used is True
    assert result.answered is True
    # Grounded prompt must contain the original (typo'd) question, not only expansions.
    grounded = [p for p in llm.prompts if p.rstrip().endswith("ANSWER:")]
    assert grounded, "expected a grounded generation prompt"
    assert original in grounded[-1]


def test_broad_exploratory_query_recovery_stays_grounded():
    """Broad exploratory wording still produces a grounded answer from recovered hits."""
    store = TopicAwareVectorStore(
        ORG, chunks=[("doc-1", "remote work policy: up to 3 days per week")]
    )
    llm = RecordingLLM(
        recovery_queries=["remote work policy days per week"],
        answer="Remote work allows up to 3 days per week. [1]",
    )
    pipe = _pipeline(llm, store)

    result = pipe.answer("Tell me about working from home options", org_id=ORG)

    assert result.recovery_used is True
    assert result.answered is True
    assert result.source == "policy"
    assert result.sources


def test_unsupported_inference_prevention_in_prompt():
    """Grounding Gap: prompt forbids inventing conclusions not in context."""
    prompt = build_grounded_prompt(
        "Can a male employee file a complaint against a female employee?",
        ["Applicable to all employees. Harassment is prohibited."],
        FALLBACK,
    )
    assert "Related but Not Explicit" in prompt or "Related but Not Explicit".lower() in prompt.lower() or "does NOT explicitly answer" in prompt
    assert "outside knowledge" in prompt.lower()
    assert "Do NOT invent" in prompt or "Unsupported conclusions" in prompt or "unsupported" in prompt.lower()
    assert FALLBACK in prompt


def test_related_but_not_explicit_response_mode_in_prompt():
    """Three modes only: Explicitly Supported / Related but Not Explicit / No Supporting Evidence."""
    prompt = build_grounded_prompt("What about parental leave?", ["leave: 25 days annual"], FALLBACK)
    assert "Explicitly Supported" in prompt
    assert "Related but Not Explicit" in prompt
    assert "No Supporting Evidence" in prompt


def test_recovery_trigger_gate_miss_and_insufficient_evidence():
    """Both architectural triggers fire; happy path does not."""
    # --- gate_miss ---
    store = TopicAwareVectorStore(ORG, chunks=[("doc-1", TARGET_CHUNK)])
    llm_gate = RecordingLLM(
        recovery_queries=["leave wellness allowance"],
        answer="Covered under wellness allowance. [1]",
    )
    r1 = _pipeline(llm_gate, store).answer("protein supplements?", org_id=ORG)
    assert r1.recovery_reason == RECOVERY_REASON_GATE_MISS
    assert llm_gate.recovery_calls == 1

    # --- insufficient_evidence: gate passes, generation finds evidence insufficient ---
    # Question contains the topic keyword "parking" so first retrieve clears the gate.
    store2 = TopicAwareVectorStore(
        ORG,
        chunks=[
            ("doc-park", "parking: employees may use lot B"),
            ("doc-leave", "leave: 25 days paid annual"),
        ],
    )
    llm_insuf = RecordingLLM(
        answers=[FALLBACK, "Employees may use lot B for parking. [1]"],
        recovery_queries=["parking lot employees"],
    )
    r2 = _pipeline(llm_insuf, store2).answer(
        "Where is employee parking located?", org_id=ORG
    )
    assert r2.recovery_reason == RECOVERY_REASON_INSUFFICIENT_EVIDENCE
    assert llm_insuf.recovery_calls == 1
    assert r2.answered is True

    # --- happy path: no recovery ---
    store3 = TopicAwareVectorStore(ORG, chunks=[("doc-1", "leave: 25 days")])
    llm_ok = RecordingLLM(answer="25 days. [1]", recovery_queries=["should not be called"])
    r3 = _pipeline(llm_ok, store3).answer("How many leave days?", org_id=ORG)
    assert r3.recovery_used is False
    assert r3.recovery_reason is None
    assert llm_ok.recovery_calls == 0
    assert r3.answered is True


def test_recovery_bound_enforcement_one_expand_only():
    """At most one recovery expand even if evidence remains insufficient after."""
    store = TopicAwareVectorStore(ORG, chunks=[("doc-1", TARGET_CHUNK)])
    # Recovery finds the chunk (gate would pass) but generation still refuses.
    llm = RecordingLLM(
        recovery_queries=["leave wellness allowance"],
        answers=[FALLBACK],  # every generate is refusal
    )
    pipe = _pipeline(llm, store)

    result = pipe.answer("protein supplements reimbursed?", org_id=ORG)

    assert llm.recovery_calls == 1
    assert result.recovery_used is True
    # Still insufficient after the single recovery → fallback (no second expand).
    assert result.answered is False
    assert result.source == "none"
    assert result.final_answer_source == "none"


def test_graceful_degradation_on_recovery_failure():
    """Expander timeout/error must not fail the request — existing path continues."""
    store = TopicAwareVectorStore(ORG, chunks=[("doc-1", TARGET_CHUNK)])
    llm = RecordingLLM(raise_on_recovery=True, answer="should not matter")
    pipe = _pipeline(llm, store)

    result = pipe.answer("protein supplements reimbursed?", org_id=ORG)

    assert result.recovery_used is True
    assert result.recovery_reason == RECOVERY_REASON_GATE_MISS
    assert result.recovery_queries == []
    assert result.answered is False
    assert result.source == "none"
    assert result.answer == FALLBACK


def test_graceful_degradation_on_garbage_recovery_output():
    """Invalid expander output → skip re-retrieve; request still completes."""
    store = TopicAwareVectorStore(ORG, chunks=[("doc-1", TARGET_CHUNK)])
    llm = RecordingLLM(
        recovery_queries=["!!!", "", "x" * 500],  # all invalid / too long / empty after parse
        answer="nope",
    )
    # Force recovery_queries that parse to empty: only duplicates of the question.
    llm = RecordingLLM(
        recovery_queries=["protein supplements reimbursed?"],  # duplicate of intent → dropped
        answer="nope",
    )
    pipe = _pipeline(llm, store)
    question = "protein supplements reimbursed?"
    result = pipe.answer(question, org_id=ORG)

    assert result.recovery_used is True
    assert result.recovery_queries == []
    assert result.answered is False
    assert result.source == "none"


def test_recovery_disabled_keeps_normal_path():
    """Kill-switch: recovery off → gate miss goes straight to fallback."""
    store = TopicAwareVectorStore(ORG, chunks=[("doc-1", TARGET_CHUNK)])
    llm = RecordingLLM(
        recovery_queries=["leave wellness"],
        answer="should not generate on gate miss",
    )
    pipe = _pipeline(llm, store, recovery=RecoverySettings(enabled=False))

    result = pipe.answer("protein supplements?", org_id=ORG)

    assert result.recovery_used is False
    assert llm.recovery_calls == 0
    assert result.answered is False
