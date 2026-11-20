"""Retrieval-reuse gate tests (Phase 8, Part 2).

Deterministic unit tests — no DB, LLM, or real embedding model (see tests/fakes.py
for why). They prove the cheap, non-LLM reuse check does exactly what it must:

- a genuine follow-up asking about NEW information (a different topic than the
  previous turn's chunks) triggers a fresh retrieval, not reuse;
- a follow-up answerable from what was already retrieved (a repeat / clarification
  about the same fact) reuses the previous chunks and skips retrieval;
- reused chunks still pass through the UNCHANGED confidence gate and grounded
  generation afterwards — reuse never bypasses or weakens the gate.

The reuse decision is a plain cosine comparison in code; ``KeywordEmbedder`` makes
that similarity fully controllable so each case is unambiguous.
"""

from __future__ import annotations

from app.config.settings import MemorySettings, RagSettings, RecoverySettings, ReuseSettings
from app.rag.pipeline import RagPipeline
from .conftest import requires_db
from .fakes import (
    InMemoryConversationStore,
    KeywordEmbedder,
    RecordingLLM,
    RecordingVectorStore,
)

ORG = "org-1"
FALLBACK = "I don't have that information."


def _pipeline(llm, store, memory, *, reuse_threshold=0.6):
    return RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(top_k=5, similarity_threshold=0.35, fallback_response=FALLBACK),
        memory=memory,
        web_search=None,
        memory_settings=MemorySettings(recent_turns=3),
        retriever=None,  # plain vector search, so query() call-count is the reuse signal
        reuse_settings=ReuseSettings(enabled=True, threshold=reuse_threshold),
        recovery_settings=RecoverySettings(enabled=False),
    )


def test_new_topic_followup_triggers_fresh_retrieval():
    # Turn 1 retrieves a LEAVE chunk. Turn 2 is genuinely about SICK leave — a
    # different topic the previous chunk does not cover — so it must retrieve fresh.
    llm = RecordingLLM(rewrite="How many paid sick days do we get?")
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, store, memory)
    cid = memory.create_conversation(ORG)

    pipe.answer("How many annual leave days do we get?", ORG, conversation_id=cid)
    assert store.query_calls == 1  # turn 1 retrieved

    r2 = pipe.answer("and sick days?", ORG, conversation_id=cid)
    assert r2.retrieval_reused is False, "new-topic follow-up must not reuse old chunks"
    assert store.query_calls == 2, "turn 2 should have run a fresh retrieval"


def test_same_fact_followup_reuses_previous_chunks():
    # Turn 2 (a clarification/repeat) rewrites to the SAME topic as turn 1's chunk,
    # so the reuse gate fires and retrieval is skipped.
    llm = RecordingLLM(rewrite="How many paid annual leave days do we get?")
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, store, memory)
    cid = memory.create_conversation(ORG)

    pipe.answer("How many annual leave days do we get?", ORG, conversation_id=cid)
    assert store.query_calls == 1

    r2 = pipe.answer("sorry, can you repeat that?", ORG, conversation_id=cid)
    assert r2.retrieval_reused is True, "same-fact follow-up should reuse previous chunks"
    assert store.query_calls == 1, "reuse must skip a fresh retrieval"
    assert any("leave" in c.content for c in r2.sources)


def test_reused_chunks_still_pass_gate_and_generation():
    # When reuse fires, the reused chunks must flow through the unchanged gate and
    # grounded-generation path: the gate score is a real cosine of THIS question vs
    # the reused chunk, and the answer is produced from a grounded prompt.
    answer = "Full-time employees get 25 days of leave. [1]"
    llm = RecordingLLM(answer=answer, rewrite="How many paid annual leave days do we get?")
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, store, memory)
    cid = memory.create_conversation(ORG)

    pipe.answer("How many annual leave days do we get?", ORG, conversation_id=cid)
    r2 = pipe.answer("what was that again?", ORG, conversation_id=cid)

    assert r2.retrieval_reused is True
    assert r2.answered is True and r2.source == "policy"
    assert r2.answer == answer
    # Gate signal is a genuine cosine similarity that cleared the 0.35 threshold.
    assert r2.top_score is not None and r2.top_score >= 0.35
    # A grounded prompt containing the reused chunk was actually generated.
    grounded = [p for p in llm.prompts if "CONTEXT:" in p and "leave: full-time" in p]
    assert grounded, "reused chunks were not fed through grounded generation"


def test_reuse_below_threshold_does_not_fire():
    # A high threshold means even a same-topic follow-up won't be trusted to reuse;
    # this guards against the gate silently firing when it shouldn't.
    llm = RecordingLLM(rewrite="How many paid annual leave days do we get?")
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, store, memory, reuse_threshold=1.01)  # impossible to clear
    cid = memory.create_conversation(ORG)

    pipe.answer("How many annual leave days do we get?", ORG, conversation_id=cid)
    r2 = pipe.answer("repeat that?", ORG, conversation_id=cid)
    assert r2.retrieval_reused is False
    assert store.query_calls == 2


def test_reuse_never_crosses_a_tenant_boundary():
    # The reuse check filters previous chunks by org_id, so a conversation can never
    # reuse another tenant's chunks even if a caller mixed conversation/org ids.
    llm = RecordingLLM(rewrite="How many paid annual leave days do we get?")
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, store, memory)
    cid = memory.create_conversation(ORG)

    pipe.answer("How many annual leave days do we get?", ORG, conversation_id=cid)
    # Same conversation but a DIFFERENT org id on the next turn: stored chunks belong
    # to ORG, so they must be ignored -> fresh retrieval under the other tenant.
    r2 = pipe.answer("repeat that?", "org-2", conversation_id=cid)
    assert r2.retrieval_reused is False
    assert store.query_calls == 2


@requires_db  # uses the real BGE-M3 embedder fixture (loaded once per session)
def test_reuse_threshold_separates_same_fact_from_new_topic_on_real_embeddings(embedder):
    """Validate the reuse threshold against the configured embedder's real
    similarities (Jina v3 today; 0.72 was tuned on BGE-M3 and reused the wrong
    passages under Jina).

    A repeat about the already-retrieved fact must embed CLOSER to the stored chunk
    than the threshold, and DIFFERENT follow-ups (another kind of leave, another
    group of employees) FURTHER, or reuse answers them from the wrong passage.
    This is the empirical check behind the documented threshold (CLAUDE.md §4).
    """
    threshold = ReuseSettings().threshold
    chunk = "Full-time employees are entitled to 25 days of paid annual leave per year."
    same_fact = "How many paid annual leave days do full-time employees get?"
    different = (
        "How many paid sick days do employees get?",
        "How many leave days do part-time employees get?",  # 0.81 under Jina v3
    )

    cvec, same_vec, *new_vecs = embedder.embed([chunk, same_fact, *different])
    same_sim = RagPipeline._cosine(same_vec, cvec)
    assert same_sim >= threshold, (
        f"same-fact follow-up should reuse (sim {same_sim:.3f} >= {threshold})"
    )
    for question, vec in zip(different, new_vecs):
        sim = RagPipeline._cosine(vec, cvec)
        assert sim < threshold, (
            f"{question!r} should retrieve fresh (sim {sim:.3f} < {threshold})"
        )


class _LongInputRejectingEmbedder(KeywordEmbedder):
    """Rejects an over-long input the way Jina does (400, 8,194-token limit)."""

    def embed(self, texts):
        if any(len(t) > 4000 for t in texts):
            raise RuntimeError("Input text exceeds the model's maximum of 8194 tokens")
        return super().embed(texts)


def test_a_long_remembered_block_does_not_fail_the_follow_up():
    # A Deep answer remembers neighbour-joined blocks; embedding one whole for the
    # next turn's reuse check failed the follow-up answer in production.
    llm = RecordingLLM(rewrite="How many paid annual leave days do we get?")
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days. " + "x" * 30_000)
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, store, memory)
    pipe._embedder = _LongInputRejectingEmbedder()
    cid = memory.create_conversation(ORG)

    pipe.answer("How many annual leave days do we get?", ORG, conversation_id=cid)
    r2 = pipe.answer("repeat that?", ORG, conversation_id=cid)
    assert r2.answer  # answered, not "Chat provider failure"


def test_a_failed_reuse_check_retrieves_fresh():
    llm = RecordingLLM(rewrite="How many paid annual leave days do we get?")
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, store, memory)
    cid = memory.create_conversation(ORG)
    pipe.answer("How many annual leave days do we get?", ORG, conversation_id=cid)

    real = pipe._embedder.embed
    calls = []

    def flaky(texts):
        calls.append(texts)
        if len(calls) == 2:  # the reuse check's embed, after the question's own
            raise RuntimeError("embedding API down")
        return real(texts)

    pipe._embedder.embed = flaky
    r2 = pipe.answer("repeat that?", ORG, conversation_id=cid)
    assert r2.retrieval_reused is False and store.query_calls == 2


def test_a_turn_with_its_own_worked_figures_never_reuses_the_last_turns_chunks():
    """Staging, Claude Haiku: "and how many of them reached an offer?" reused
    the previous turn's chunks, its figure among them, and refused."""
    from app.insights.table_answers import TableAnswer, reset_answers, use_answers

    llm = RecordingLLM(rewrite="How many paid annual leave days do we get?")
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, store, memory)
    cid = memory.create_conversation(ORG)
    pipe.answer("How many annual leave days do we get?", ORG, conversation_id=cid)

    token = use_answers([TableAnswer(provider="google", document_id="d", document_title="t",
                                     source_uri=None, text="Figures: 43")])
    try:
        r2 = pipe.answer("sorry, can you repeat that?", ORG, conversation_id=cid)
    finally:
        reset_answers(token)
    assert r2.retrieval_reused is False and store.query_calls == 2


# -- the reuse re-check: access and freshness --------------------------------
# Sharing can be revoked and a page edited between two questions; reusing the
# remembered chunk then would quote what the asker may no longer read, or an
# old version. Each case must search fresh instead.


def _two_turns(store, *, between=lambda store, memory, cid: None):
    llm = RecordingLLM(rewrite="How many paid annual leave days do we get?")
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, store, memory)
    cid = memory.create_conversation(ORG)
    pipe.answer("How many annual leave days do we get?", ORG, conversation_id=cid)
    between(store, memory, cid)
    return pipe.answer("sorry, can you repeat that?", ORG, conversation_id=cid)


def test_reuse_still_fires_when_the_chunk_is_readable_and_unchanged():
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")
    r2 = _two_turns(store)
    assert r2.retrieval_reused is True and store.query_calls == 1


def test_a_document_the_asker_can_no_longer_open_is_not_reused():
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")

    def revoke(store, memory, cid):
        store.hidden = True

    r2 = _two_turns(store, between=revoke)
    assert r2.retrieval_reused is False and store.query_calls == 2


def test_a_document_re_synced_since_the_last_turn_is_not_reused():
    from datetime import datetime, timedelta, timezone

    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")

    def resync(store, memory, cid):
        store.resynced_at = datetime.now(timezone.utc) + timedelta(seconds=1)

    r2 = _two_turns(store, between=resync)
    assert r2.retrieval_reused is False and store.query_calls == 2


def test_a_remembered_chunk_without_a_save_time_is_not_reused():
    from dataclasses import replace

    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")

    def forget_time(store, memory, cid):
        memory._last[cid] = [replace(r, stored_at=None) for r in memory._last[cid]]

    r2 = _two_turns(store, between=forget_time)
    assert r2.retrieval_reused is False and store.query_calls == 2


def test_the_re_check_passes_the_asker_and_scope_to_the_store():
    from app.vectorstore.base import Viewer

    seen = {}
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")
    real = store.chunks_at

    def spy(org_id, positions, *, workspace_id=None, viewer=None, unchanged_since=None):
        seen.update(workspace_id=workspace_id, viewer=viewer, since=unchanged_since)
        return real(org_id, positions, workspace_id=workspace_id, viewer=viewer,
                    unchanged_since=unchanged_since)

    store.chunks_at = spy
    llm = RecordingLLM(rewrite="How many paid annual leave days do we get?")
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, store, memory)
    cid = memory.create_conversation(ORG)
    asker = Viewer(email="a@example.com")
    pipe.answer("How many annual leave days do we get?", ORG, conversation_id=cid,
                workspace_id="ws-1", viewer=asker)
    pipe.answer("sorry, can you repeat that?", ORG, conversation_id=cid,
                workspace_id="ws-1", viewer=asker)
    assert seen["workspace_id"] == "ws-1" and seen["viewer"] == asker
    assert seen["since"] is not None


def test_chunks_at_drops_chunks_written_after_unchanged_since():
    """Real SQL: a re-sync rewrites a document's chunks, so ``unchanged_since``
    before that write hides them and after it keeps them."""
    import os
    import uuid
    from datetime import datetime, timedelta, timezone

    import pytest

    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs a database")
    from app.db.connection import get_connection
    from app.vectorstore.pgvector_store import PgVectorStore

    store = PgVectorStore()
    vector = [0.0] * int(os.getenv("EMBEDDING_DIM", "1024"))
    vector[0] = 1.0
    org = store.create_organization(f"reuse-test-{uuid.uuid4().hex[:8]}")
    try:
        doc = store.add_document(org, "Leave", ["leave: 25 days"], [vector])
        positions = [(doc, 0)]
        with get_connection() as conn:
            db_now = conn.execute("SELECT now()").fetchone()[0]
        assert store.chunks_at(org, positions, unchanged_since=db_now + timedelta(hours=1))
        assert store.chunks_at(org, positions, unchanged_since=db_now - timedelta(hours=1)) == []
        assert store.chunks_at(org, positions)  # no bound: unchanged behaviour
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org,))
