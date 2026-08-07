"""RagPipeline.answer_stream / PolicyAgent.answer_stream.

Deterministic unit tests with fakes — no DB, real LLM, or embedding model
(same convention as test_recovery.py). Proves that streaming: (1) yields
the identical text ``answer()`` would return, just chunked, (2) never streams
a fallback/refusal any differently than a normal answer, and (3) the agent
layer's citations/metadata match a plain ``answer()`` call for the same
question.
"""

from __future__ import annotations

from app.agent.policy_agent import PolicyAgent
from app.config.settings import RagSettings, RecoverySettings, ReuseSettings
from app.rag.pipeline import RagPipeline

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


def test_answer_stream_with_tagged_mode_b_still_chunks_correctly():
    """A tagged MODE: B reply that already passes tone compliance streams the
    parsed answer text (tag stripped), matching the non-streaming path."""
    tagged = "MODE: B\n\nYou have annual leave (25 days) [1]. Check with HR for parental leave."
    llm = RecordingLLM(answer=tagged)
    pipeline = _pipeline(llm, _store_with_hit())

    direct = pipeline.answer("What about parental leave?", ORG)

    llm2 = RecordingLLM(answer=tagged)
    pipeline2 = _pipeline(llm2, _store_with_hit())
    chunks, streamed = pipeline2.answer_stream("What about parental leave?", ORG)

    assert "".join(chunks) == direct.answer == streamed.answer
    assert "MODE:" not in streamed.answer
    assert streamed.response_mode == "B"


# -- the shared chunker (app/core/streaming.py) -------------------------------
#
# RagPipeline and GitHubAgent carried byte-identical copies of this loop. It now
# lives in one place so the two agents cannot drift on how text reaches SSE —
# the *reasoning* for chunking a finished answer differs per agent and stays in
# their docstrings; the mechanics do not.


def test_chunk_answer_reassembles_to_the_original_text():
    from app.core.streaming import chunk_answer

    text = "Full-time employees receive 25 days of paid annual leave per year."

    assert "".join(chunk_answer(text, 7)) == text
    assert all(len(c) <= 7 for c in chunk_answer(text, 7))


def test_chunk_answer_handles_the_edges_the_inline_copies_did_not():
    """Empty text and a non-positive size were unguarded in both copies.

    ``range(0, n, 0)`` raises and a negative step loops forever, so a caller
    passing 0 would have hung an SSE stream rather than getting the answer.
    """
    from app.core.streaming import chunk_answer

    assert list(chunk_answer("", 40)) == []
    assert list(chunk_answer("hello", 0)) == ["hello"]
    assert list(chunk_answer("hello", -5)) == ["hello"]


def test_a_chunk_size_larger_than_the_text_yields_one_piece():
    from app.core.streaming import chunk_answer

    assert list(chunk_answer("short", 1000)) == ["short"]
