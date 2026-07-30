"""Phase 19: request budget, model routing, token logging, query cache."""

from __future__ import annotations

import json
import logging
import time

import pytest

from app.config.settings import (
    QueryCacheSettings,
    RagSettings,
    RecoverySettings,
    RequestBudgetSettings,
    ReuseSettings,
)
from app.llm.usage import TokenUsage
from app.rag.pipeline import RagPipeline
from app.rag.query_cache import QueryAnswerCache, normalize_question
from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

ORG = "org-phase19"
FALLBACK = "I don't have information on that in the available policy documents."


class StagedRecordingLLM(RecordingLLM):
    """Records which provider instance handled each aux vs main stage."""

    def __init__(self, *, label: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.model = f"recording-{label}"


def _pipeline(
    main: RecordingLLM,
    aux: RecordingLLM,
    store: TopicAwareVectorStore,
    *,
    budget: RequestBudgetSettings | None = None,
    cache: QueryAnswerCache | None = None,
) -> RagPipeline:
    return RagPipeline(
        llm=main,
        llm_aux=aux,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(top_k=3, similarity_threshold=0.35, fallback_response=FALLBACK),
        memory=None,
        web_search=None,
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=True, max_queries=1),
        budget_settings=budget,
        query_cache=cache,
    )


def test_aux_model_used_for_recovery_not_generation():
    main = StagedRecordingLLM(label="main", answer="MODE: A\n\nMain model answer.")
    aux = StagedRecordingLLM(
        label="aux",
        recovery_queries=["wellness allowance supplements"],
        answer="MODE: A\n\nShould not be used for recovery path.",
    )
    store = TopicAwareVectorStore(
        ORG,
        chunks=[("d1", "wellness allowance covers supplements")],
    )
    pipe = _pipeline(main, aux, store)
    result = pipe.answer("Can I get protein supplements reimbursed?", org_id=ORG)
    assert result.recovery_used is True
    assert aux.recovery_calls >= 1
    assert main.grounded_calls >= 1


def test_request_budget_skips_recovery_when_exhausted():
    main = StagedRecordingLLM(label="main", answer="nope")
    aux = StagedRecordingLLM(label="aux", recovery_queries=["wellness"])
    store = TopicAwareVectorStore(ORG, chunks=[("d1", "wellness allowance")])
    budget = RequestBudgetSettings(deadline_seconds=0.01, min_stage_seconds=3.0)
    pipe = _pipeline(main, aux, store, budget=budget)
    time.sleep(0.02)
    result = pipe.answer("protein supplements?", org_id=ORG)
    assert result.recovery_used is False
    assert aux.recovery_calls == 0


from .conftest import requires_db


@requires_db
def test_query_cache_hit_skips_retrieval(store, embedder, org_cleanup):
    main = StagedRecordingLLM(label="main", answer="MODE: A\n\nCached path.")
    aux = StagedRecordingLLM(label="aux")
    org_id = store.create_organization("CacheTestOrg")
    org_cleanup.append(org_id)
    text = "Full-time employees receive 25 days of paid annual leave per year."
    store.add_document(org_id, "leave", [text], embedder.embed([text]))
    from app.rag.retrieval import HybridRetriever

    retriever = HybridRetriever(store=store, reranker=None)
    cache = QueryAnswerCache(QueryCacheSettings(enabled=True, ttl_seconds=600))
    pipe = RagPipeline(
        llm=main,
        llm_aux=aux,
        embedder=embedder,
        store=store,
        settings=RagSettings(top_k=3, similarity_threshold=0.35, fallback_response=FALLBACK),
        memory=None,
        web_search=None,
        retriever=retriever,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
        query_cache=cache,
    )
    q = "How many leave days?"
    first = pipe.answer(q, org_id=org_id)
    assert first.cache_hit is False
    calls_before = main.grounded_calls
    second = pipe.answer(q, org_id=org_id)
    assert second.cache_hit is True
    assert main.grounded_calls == calls_before
    assert second.answer == first.answer


def test_query_cache_key_normalization():
    assert normalize_question("  How   Many Days? ") == normalize_question("how many days?")


def test_token_logging_emits_json(caplog):
    caplog.set_level(logging.INFO, logger="rag.llm_usage")
    main = StagedRecordingLLM(label="main", answer="MODE: A\n\nOK.")
    main.last_usage = TokenUsage(input_tokens=100, output_tokens=20)
    aux = StagedRecordingLLM(label="aux")
    store = TopicAwareVectorStore(ORG, chunks=[("d1", "leave policy text")])
    pipe = _pipeline(main, aux, store)
    pipe.answer("leave policy?", org_id=ORG)
    llm_logs = [r for r in caplog.records if r.name == "rag.llm_usage"]
    assert llm_logs
    payload = json.loads(llm_logs[0].message)
    assert payload["event"] == "llm_call"
    assert payload.get("input_tokens") == 100
