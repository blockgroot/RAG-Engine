"""Benchmark 1 accuracy fixes: neighbour chunks, the focus rule, up-front rephrasing.

Fakes only, except the last test, which checks the neighbour read's SQL against
a real database the same way ``test_doc_access`` does.
"""

from __future__ import annotations

import os
from dataclasses import replace

from app.config.settings import QueryNormSettings, RagSettings, RecoverySettings, ReuseSettings
from app.rag.pipeline import RagPipeline
from app.rag.prompts import build_grounded_prompt
from app.vectorstore.base import RetrievedChunk, Viewer
from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

ORG = "org-depth"
FALLBACK = "I don't have information on that in the available policy documents."
TARGET = "leave wellness allowance covers health-related products and supplements"


class NeighbourStore(TopicAwareVectorStore):
    """Adds ``chunks_at`` over a fixed ``{(doc, index): text}`` map and records the viewer."""

    def __init__(self, positions: dict[tuple[str, int], str], **kw) -> None:
        super().__init__(ORG, **kw)
        self._positions = positions
        self.viewers: list = []

    def chunks_at(self, org_id, positions, *, workspace_id=None, viewer=None):
        self.viewers.append(viewer)
        return [
            RetrievedChunk(content=self._positions[p], score=0.0, document_id=p[0],
                           chunk_index=p[1], org_id=org_id)
            for p in positions if p in self._positions
        ]


def _pipeline(store, llm=None, *, neighbors=0, proactive=False) -> RagPipeline:
    return RagPipeline(
        llm=llm or RecordingLLM(),
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(top_k=5, fallback_response=FALLBACK, neighbor_chunks=neighbors),
        memory=None,
        web_search=None,
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=True, max_queries=2, proactive=proactive),
        query_norm_settings=QueryNormSettings(enabled=False, max_edit_distance=1, min_word_length=4),
    )


def _hit(doc: str, idx: int, text: str) -> RetrievedChunk:
    return RetrievedChunk(content=text, score=0.8, document_id=doc, chunk_index=idx, org_id=ORG)


def test_neighbours_join_the_best_hits_in_reading_order_without_repeats():
    store = NeighbourStore({("a", 4): "A4", ("a", 6): "A6", ("b", 1): "B1", ("b", 3): "B3"})
    pipe = _pipeline(store, neighbors=1)
    viewer = Viewer(email="x@example.com")
    hits = [_hit("a", 5, "A5"), _hit("b", 2, "B2"), _hit("a", 6, "A6 as its own hit"), _hit("c", 0, "C0")]

    out = pipe._with_neighbors(hits, ORG, workspace_id=None, viewer=viewer)

    # Top two hits carry their neighbours; A6 was absorbed into block 1, so it
    # is not repeated as its own block; the rest keep their order.
    assert [h.content for h in out] == ["A4\nA5\nA6", "B1\nB2\nB3", "C0"]
    assert [(h.document_id, h.chunk_index) for h in out] == [("a", 5), ("b", 2), ("c", 0)]
    assert out[0].score == 0.8  # never touches a score, so never the gate
    assert store.viewers == [viewer]  # the read is filtered for the asker


def test_neighbours_off_or_unsupported_leave_hits_unchanged():
    hits = [_hit("a", 5, "A5")]
    assert _pipeline(NeighbourStore({("a", 4): "A4"}), neighbors=0)._with_neighbors(
        hits, ORG, workspace_id=None, viewer=None) == hits
    # A store without the capability (the base raises) costs nothing.
    assert _pipeline(TopicAwareVectorStore(ORG), neighbors=1)._with_neighbors(
        hits, ORG, workspace_id=None, viewer=None) == hits


def test_neighbours_reach_the_answer_prompt():
    store = NeighbourStore({("doc-1", 1): "the allowance renews every April"},
                           chunks=[("doc-1", TARGET)])
    llm = RecordingLLM(answer="MODE: A\n\nIt covers supplements. [1]")
    _pipeline(store, llm, neighbors=1).answer("What does the leave wellness allowance cover?", org_id=ORG)
    grounded = [p for p in llm.prompts if "CONTEXT:" in p and "QUESTION:" in p]
    assert grounded and "renews every April" in grounded[-1]


def test_focus_rule_is_off_by_default_and_keeps_the_mode_tag_first():
    base = build_grounded_prompt("q?", ["ctx"], FALLBACK)
    ruled = build_grounded_prompt("q?", ["ctx"], FALLBACK, focus_rule=True)
    assert "Never attach a fact from one document" not in base
    assert "Never attach a fact from one document" in ruled
    # The rule is the only difference, and it sits before CONTEXT (CLAUDE.md:
    # do not move CONTEXT or QUESTION earlier; MODE stays the first instruction).
    start, end = ruled.index("6. CONTEXT blocks"), ruled.index("\nCONTEXT:\n<<<")
    assert ruled[:start] + ruled[end:] == base


def test_proactive_rephrase_finds_a_document_worded_differently():
    # The question shares no topic word with the chunk; the rephrase does.
    store = TopicAwareVectorStore(ORG, chunks=[("doc-1", TARGET)])
    llm = RecordingLLM(recovery_queries=["leave wellness allowance supplements"],
                       answer="MODE: A\n\nSupplements are covered. [1]")
    result = _pipeline(store, llm, proactive=True).answer(
        "Can I get protein shakes reimbursed?", org_id=ORG)
    assert result.answered is True
    assert result.recovery_used is False  # found up front, not by after-the-fact recovery
    assert len(llm.recovery_prompts) == 1


def test_proactive_off_makes_no_rephrase_call_when_search_succeeds():
    store = TopicAwareVectorStore(ORG, chunks=[("doc-1", TARGET)])
    llm = RecordingLLM(answer="MODE: A\n\nSupplements are covered. [1]")
    _pipeline(store, llm).answer("What does the leave wellness allowance cover?", org_id=ORG)
    assert llm.recovery_prompts == []


def test_chunks_at_is_scoped_by_org_and_viewer():
    """Real SQL: a neighbour read must not cross tenants or reveal a private document."""
    import uuid

    import pytest

    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs a database")
    from app.db.connection import get_connection
    from app.vectorstore.pgvector_store import PgVectorStore

    store = PgVectorStore()
    vector = [0.0] * int(os.getenv("EMBEDDING_DIM", "1024"))
    vector[0] = 1.0
    org = store.create_organization(f"depth-test-{uuid.uuid4().hex[:8]}")
    other = store.create_organization(f"depth-test-{uuid.uuid4().hex[:8]}")
    try:
        for org_id, ext, public in ((org, "pub", True), (org, "priv", False), (other, "pub", True)):
            store.upsert_source_document(
                org_id, provider="google", external_id=ext, title=ext,
                chunks=[f"{ext} zero", f"{ext} one"], embeddings=[vector, vector],
                is_public=public, viewers=None if public else ["owner@example.com"],
            )
        docs = {h.document_title: h.document_id for h in store.query(org, vector, top_k=10)}
        positions = [(docs["pub"], 1), (docs["priv"], 1)]
        outsider = Viewer(email="other@example.com")

        assert {c.content for c in store.chunks_at(org, positions, viewer=outsider)} == {"pub one"}
        assert {c.content for c in store.chunks_at(org, positions, viewer=None)} == {"pub one", "priv one"}
        assert store.chunks_at(other, positions, viewer=None) == []  # another org's ids
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = ANY(%s::uuid[])", ([org, other],))


def test_weak_passage_cutoff_keeps_the_best_and_is_off_by_default():
    from app.rag.retrieval import _drop_weak

    hits = [replace(_hit("a", 0, "A"), rerank_score=s) for s in (0.9, 0.5, 0.2)]
    hits[1] = replace(hits[1], document_id="b")
    hits[2] = replace(hits[2], document_id="c")
    assert _drop_weak(hits, 0.0) == hits  # off
    assert [h.document_id for h in _drop_weak(hits, 0.5)] == ["a", "b"]  # 0.2 < 0.45
    assert [h.document_id for h in _drop_weak(hits, 0.99)] == ["a"]  # never empties
    assert [h.score for h in _drop_weak(hits, 0.5)] == [0.8, 0.8]  # cosine untouched
    # No reranker scores (fakes, reused hits) or a non-positive best: no-op.
    assert _drop_weak([_hit("a", 0, "A"), _hit("b", 0, "B")], 0.5) == [_hit("a", 0, "A"), _hit("b", 0, "B")]
    neg = [replace(_hit("a", 0, "A"), rerank_score=-1.0), replace(_hit("b", 0, "B"), rerank_score=-3.0)]
    assert _drop_weak(neg, 0.5) == neg


def _scored(doc: str, idx: int, s: float) -> RetrievedChunk:
    return replace(_hit(doc, idx, f"{doc}{idx}"), rerank_score=s)


def test_spread_keeps_a_narrow_question_exactly_as_before():
    from app.rag.retrieval import _spread

    # One document far ahead of the rest: the answer lives in one place.
    hits = [_scored("a", i, s) for i, s in enumerate((0.9, 0.8, 0.7, 0.6, 0.5, 0.4))] + [_scored("b", 0, 0.1)]
    assert _spread(hits, 5, 10, 0.5, 2) == hits[:5]
    assert _spread(hits, 5, 0, 0.5, 2) == hits[:5]  # off
    assert _spread(hits, 5, 10, 0.0, 2) == hits[:5]  # off


def test_spread_widens_across_documents_when_evidence_is_spread():
    from app.rag.retrieval import _spread

    hits = [_scored("a", 0, 0.9), _scored("a", 1, 0.85), _scored("a", 2, 0.8), _scored("b", 0, 0.7),
            _scored("c", 0, 0.65), _scored("b", 1, 0.6), _scored("d", 0, 0.55), _scored("e", 0, 0.1)]
    out = _spread(hits, 3, 6, 0.5, 2)
    # The usual top 3 stay; then one passage from each new strong document
    # (b, c, d) before any second passage; e is weak and left out.
    assert [(h.document_id, h.chunk_index) for h in out] == [("a", 0), ("a", 1), ("a", 2), ("b", 0), ("c", 0), ("d", 0)]
    # With room left, a second passage per strong document follows (b1).
    assert ("b", 1) in [(h.document_id, h.chunk_index) for h in _spread(hits, 3, 7, 0.5, 2)]
    # A wide read is always a superset of the narrow one.
    for args in ((3, 6, 0.5, 1), (3, 8, 0.3, 2), (5, 10, 0.6, 2)):
        assert _spread(hits, *args)[: args[0]] == hits[: args[0]]


def test_wide_read_budget_and_whole_read_limit():
    s = RagSettings(top_k=5, max_context_chars=6000, wide_max_hits=10, wide_doc_ratio=0.5)
    assert s.ranked_max_hits == 10
    assert s.context_chars_for(5) == 6000 and s.context_chars_for(8) == 12000
    assert RagSettings(top_k=5, wide_max_hits=10).ranked_max_hits == 5  # ratio 0 = off
    assert RagSettings(top_k=5, max_context_chars=6000, wide_max_hits=10, wide_doc_ratio=0.5,
                       wide_max_context_chars=9000).context_chars_for(8) == 9000


def test_partial_and_conflict_rules_are_off_by_default_and_numbered_after_focus():
    base = build_grounded_prompt("q?", ["ctx"], FALLBACK)
    full = build_grounded_prompt("q?", ["ctx"], FALLBACK, focus_rule=True, partial_rule=True, conflict_rule=True)
    assert "answers only part" not in base and "disagree about the same thing" not in base
    assert "6. CONTEXT blocks" in full and "7. If CONTEXT answers only part" in full
    assert "8. If CONTEXT blocks disagree" in full
    assert full.index("8. If CONTEXT blocks disagree") < full.index("\nCONTEXT:\n<<<")
    only_partial = build_grounded_prompt("q?", ["ctx"], FALLBACK, partial_rule=True)
    assert "6. If CONTEXT answers only part" in only_partial



def test_rerank_with_title_shows_titles_to_the_reranker_only():
    from app.config.settings import RetrievalSettings
    from app.rag.retrieval import HybridRetriever

    seen: list[list[str]] = []

    class Recorder:
        def rerank(self, query, candidates, top_k):
            seen.append([c.content for c in candidates])
            return [replace(c, rerank_score=1.0 - i / 10) for i, c in enumerate(candidates[:top_k])]

    hits = [replace(_hit("a", 0, "body a"), document_title="Atlas launch plan"), _hit("b", 0, "body b")]
    on = HybridRetriever(TopicAwareVectorStore(ORG), reranker=Recorder(),
                         settings=RetrievalSettings(rerank_with_title=True), rag_settings=RagSettings(top_k=2))
    out = on._rerank("q", hits, 2)
    assert seen[-1] == ["Atlas launch plan\nbody a", "body b"]
    assert [h.content for h in out] == ["body a", "body b"]  # the prompt never sees the title line
    assert [h.rerank_score for h in out] == [1.0, 0.9]
    off = HybridRetriever(TopicAwareVectorStore(ORG), reranker=Recorder(),
                          settings=RetrievalSettings(), rag_settings=RagSettings(top_k=2))
    off._rerank("q", hits, 2)
    assert seen[-1] == ["body a", "body b"]


def test_deep_read_reads_the_top_documents_from_their_start_and_once_each():
    positions = {("a", i): f"A{i}" for i in range(6)} | {("b", i): f"B{i}" for i in range(6)}
    store = NeighbourStore(positions)
    pipe = _pipeline(store, neighbors=3)
    pipe._settings = replace(pipe._settings, neighbor_top_docs=2)
    hits = [_hit("a", 1, "A1"), _hit("a", 4, "A4"), _hit("b", 2, "B2"), _hit("c", 0, "C0")]

    out = pipe._with_neighbors(hits, ORG, workspace_id=None, viewer=Viewer(email="x@example.com"))

    # One block per top document, read from its start (7 pieces asked, 6 exist);
    # A4 was absorbed into a's block, c is not a top document and stays as is.
    assert [h.content for h in out] == ["A0\nA1\nA2\nA3\nA4\nA5", "B0\nB1\nB2\nB3\nB4\nB5", "C0"]
    assert out[0].score == 0.8  # scores untouched, so the gate is too


def test_default_neighbours_are_unchanged_by_the_deep_read_setting():
    store = NeighbourStore({("a", 0): "A0", ("a", 1): "A1", ("a", 2): "A2"})
    out = _pipeline(store, neighbors=1)._with_neighbors([_hit("a", 0, "A0")], ORG, workspace_id=None, viewer=None)
    assert out[0].content == "A0\nA1"  # ±1 as before, not shifted to 3 pieces


def test_deep_analysis_mode_deepens_only_its_own_request():
    from app.rag.pipeline import reset_deep_read, use_deep_read

    store = NeighbourStore({("a", i): f"A{i}" for i in range(4)})
    pipe = _pipeline(store, neighbors=0)  # normal Ask: no neighbours at all
    hits = [_hit("a", 1, "A1")]
    assert pipe._with_neighbors(hits, ORG, workspace_id=None, viewer=None) == hits

    token = use_deep_read(True)
    try:
        out = pipe._with_neighbors(hits, ORG, workspace_id=None, viewer=None)
        assert out[0].content == "A0\nA1\nA2\nA3"  # the document read from its start
        assert pipe._read_settings().context_chars_for(10) == 30000  # one deep budget
    finally:
        reset_deep_read(token)
    assert pipe._with_neighbors(hits, ORG, workspace_id=None, viewer=None) == hits


def test_deep_analysis_reads_a_mid_sized_upload_whole():
    from app.config.settings import AttachmentSettings
    from app.rag.pipeline import reset_deep_read, use_deep_read

    pipe = _pipeline(NeighbourStore({}), neighbors=0)
    upload = [("notes.txt", "x" * 20_000, False)]
    settings = replace(AttachmentSettings.from_env(), inline_char_budget=12_000)
    token = use_deep_read(True)
    try:
        blocks = pipe.attachment_contexts("summarise", upload, settings=settings)
    finally:
        reset_deep_read(token)
    assert len(blocks) == 1 and blocks[0].endswith("x" * 20_000)  # inlined, no paging call
