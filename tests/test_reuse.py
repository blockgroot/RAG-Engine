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

from app.config.settings import MemorySettings, RagSettings, ReuseSettings
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
    """Validate the 0.60 starting threshold against real BGE-M3 similarities.

    A repeat/clarification about the already-retrieved fact must embed CLOSER to the
    stored chunk than the reuse threshold, while a genuinely new-topic follow-up
    embeds FURTHER — otherwise 0.60 would over- or under-trigger reuse in practice.
    This is the empirical check behind the documented threshold (see CLAUDE.md §4).
    """
    threshold = ReuseSettings().threshold
    chunk = "Full-time employees are entitled to 25 days of paid annual leave per year."
    same_fact = "How many paid annual leave days do full-time employees get?"
    new_topic = "How many paid sick days do employees get?"

    cvec, same_vec, new_vec = embedder.embed([chunk, same_fact, new_topic])
    same_sim = RagPipeline._cosine(same_vec, cvec)
    new_sim = RagPipeline._cosine(new_vec, cvec)

    assert same_sim >= threshold, (
        f"same-fact follow-up should reuse (sim {same_sim:.3f} >= {threshold})"
    )
    assert new_sim < threshold, (
        f"new-topic follow-up should retrieve fresh (sim {new_sim:.3f} < {threshold})"
    )
