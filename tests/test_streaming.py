"""Phase 13: RagPipeline.answer_stream / PolicyAgent.answer_stream.

Deterministic unit tests with fakes — no DB, real LLM, or embedding model
(same convention as tests/test_recovery.py). Proves that streaming: (1) yields
the identical text ``answer()`` would return, just chunked, (2) never streams
a fallback/refusal any differently than a normal answer, and (3) the agent
layer's citations/metadata match a plain ``answer()`` call for the same
question.
"""

from __future__ import annotations

from app.agent.policy_agent import PolicyAgent
from app.config.settings import RagSettings, RecoverySettings, ReuseSettings
from app.rag.pipeline import RagPipeline
from app.vectorstore.base import RetrievedChunk

from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

ORG = "org-streaming"
FALLBACK = "I don't have information on that in the available policy documents."


def _pipeline(llm: RecordingLLM, store: TopicAwareVectorStore) -> RagPipeline:
    return RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(top_k=3, similarity_threshold=0.35, fallback_response=FALLBACK),
        memory=None,
        web_search=None,
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
    )


def _store_with_hit() -> TopicAwareVectorStore:
    return TopicAwareVectorStore(
        ORG, [("doc-1", "Employees get 25 days of paid annual leave per year.")]
    )


def test_answer_stream_yields_the_same_text_as_answer_chunked():
    llm = RecordingLLM(answer="You get 25 days of annual leave. [1]")
    store = _store_with_hit()
    pipeline = _pipeline(llm, store)

    direct = pipeline.answer("How many annual leave days do I get?", ORG)

    llm2 = RecordingLLM(answer="You get 25 days of annual leave. [1]")
    pipeline2 = _pipeline(llm2, _store_with_hit())
    chunks, streamed_result = pipeline2.answer_stream(
        "How many annual leave days do I get?", ORG
    )
    reassembled = "".join(chunks)

    assert reassembled == direct.answer == streamed_result.answer
    assert streamed_result.answered == direct.answered
    assert streamed_result.source == direct.source


def test_answer_stream_chunk_size_is_respected():
    llm = RecordingLLM(answer="A" * 100)
    pipeline = _pipeline(llm, _store_with_hit())

    chunks, result = pipeline.answer_stream(
        "How many annual leave days do I get?", ORG, chunk_chars=10
    )
    chunk_list = list(chunks)

    assert "".join(chunk_list) == result.answer
    assert all(len(c) <= 10 for c in chunk_list)
    assert len(chunk_list) == 10  # 100 chars / 10 per chunk


def test_answer_stream_on_gate_miss_yields_fallback_unchanged():
    llm = RecordingLLM()
    # A chunk sharing no topic keyword with the query -> cosine 0.0 -> gate miss.
    store = TopicAwareVectorStore(ORG, [("doc-1", "remote work equipment stipend")])
    pipeline = _pipeline(llm, store)

    chunks, result = pipeline.answer_stream("What is the capital of France?", ORG)
    reassembled = "".join(chunks)

    assert reassembled == FALLBACK
    assert result.answer == FALLBACK
    assert result.answered is False
    assert result.source == "none"
    # The gate short-circuit must mean no grounded-generation LLM call happened.
    assert llm.grounded_calls == 0


def test_policy_agent_answer_stream_matches_answer_citations():
    llm = RecordingLLM(answer="You get 25 days of annual leave. [1]")
    pipeline = _pipeline(llm, _store_with_hit())
    agent = PolicyAgent(pipeline)

    direct = agent.answer("How many annual leave days do I get?", ORG)

    llm2 = RecordingLLM(answer="You get 25 days of annual leave. [1]")
    agent2 = PolicyAgent(_pipeline(llm2, _store_with_hit()))
    chunks, streamed = agent2.answer_stream("How many annual leave days do I get?", ORG)

    assert "".join(chunks) == direct.answer == streamed.answer
    assert [c.reference for c in streamed.citations] == [c.reference for c in direct.citations]
    assert streamed.grounded == direct.grounded
