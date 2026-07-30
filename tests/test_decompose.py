"""Compound-question decomposition tests (Phase 18)."""

from __future__ import annotations

from app.config.settings import DecomposeSettings, RagSettings, RecoverySettings, ReuseSettings, RetrievalSettings
from app.rag.decompose import looks_compound, parse_sub_questions
from app.rag.pipeline import RagPipeline
from app.rag.retrieval import HybridRetriever
from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

ORG = "org-decompose"
FALLBACK = "I don't have information on that in the available policy documents."

SUPPLEMENTS = (
    "Wellness allowance: protein supplements and vitamins are eligible for reimbursement."
)
OTHER_ITEMS = (
    "Wellness allowance permissible items include gym membership and therapy apps. "
    "Non-permissible: cosmetic surgery, spa treatments, and luxury goods."
)


def test_heuristic_single_intent_with_and():
    q = "What is the paid annual leave allowance for full-time and part-time employees?"
    assert looks_compound(q) is False


def test_heuristic_detects_compound():
    q = "Can I get protein supplements reimbursed, and what else can I get reimbursed?"
    assert looks_compound(q) is True


def test_parse_sub_questions():
    raw = "Can protein supplements be reimbursed?\nWhat other items are reimbursable?"
    subs = parse_sub_questions(raw, original="compound?")
    assert len(subs) == 2


def _pipeline(llm: RecordingLLM, store: TopicAwareVectorStore) -> RagPipeline:
    retriever = HybridRetriever(
        store=store,
        reranker=None,
        settings=RetrievalSettings(hybrid_enabled=False, rerank_enabled=False),
    )
    return RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(top_k=5, similarity_threshold=0.35, fallback_response=FALLBACK),
        memory=None,
        web_search=None,
        retriever=retriever,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
        decompose_settings=DecomposeSettings(enabled=True),
    )


def test_single_question_skips_decompose_llm():
    store = TopicAwareVectorStore(ORG, chunks=[("d1", SUPPLEMENTS)])
    llm = RecordingLLM(answer="MODE: A\n\nSupplements are covered.")
    pipe = _pipeline(llm, store)
    result = pipe.answer("How many sick days do employees get?", org_id=ORG)
    assert llm.decompose_calls == 0
    assert result.question_decomposed is False


def test_compound_question_retrieves_both_chunks():
    store = TopicAwareVectorStore(
        ORG,
        chunks=[("d1", SUPPLEMENTS), ("d2", OTHER_ITEMS)],
    )
    llm = RecordingLLM(
        decompose_subquestions=[
            "Are protein supplements covered under the wellness allowance?",
            "What other wellness allowance items are reimbursable?",
        ],
        answer=(
            "MODE: A\n\nProtein supplements are eligible. Permissible items include "
            "gym and therapy; cosmetics and spa are not."
        ),
    )
    pipe = _pipeline(llm, store)
    compound = (
        "Can I get protein supplements reimbursed, and what else can I get reimbursed?"
    )
    result = pipe.answer(compound, org_id=ORG)

    assert llm.decompose_calls == 1
    assert result.question_decomposed is True
    assert len(result.sub_questions) == 2
    contents = " ".join(s.content.lower() for s in result.sources)
    assert "protein" in contents or "supplement" in contents
    assert "permissible" in contents or "gym" in contents
    assert store.query_calls >= 2
