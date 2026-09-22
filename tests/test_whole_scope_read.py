"""A tag-scoped question reads its whole corpus, not the best-ranked slice.

The live failure this fixes: "Summarise the overall discussion in the channel"
was answered from ONE thread. A real #rag-updates held 25 threads / 38 chunks,
`RAG_TOP_K=5` put 13% of them in front of the model, and the thread titled
"Phase 1: High-Impact UX Parity" scored highest against a request for goals --
so the bot answered confidently and partially, twice, with nothing to signal it.

Deliberately NOT keyed on the question. "Is this a summary request?" needs a
word list ("overall", "summarise") that misses "what's been going on here", or
a classifier that costs a call and fails silently. Whether the corpus FITS is a
fact, it needs no interpreting, and it fixes ordinary questions too.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config.settings import RagSettings
from app.rag.pipeline import RagPipeline, _read_order
from app.vectorstore.base import RetrievedChunk


def _chunk(doc: str, score: float, day: int, content: str = "x" * 100) -> RetrievedChunk:
    return RetrievedChunk(
        content=content,
        score=score,
        document_id=doc,
        chunk_index=0,
        org_id="o",
        # `day` is an offset, not a calendar day: a 38-chunk case would
        # otherwise run past the end of the month.
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc)
        + timedelta(days=day),
    )


class _Store:
    """Records the top_k it was asked for, so the widening is observable."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.asked_top_k: list[int] = []

    def query(self, org_id, query_vec, *, top_k=5, **kw):
        self.asked_top_k.append(top_k)
        return list(self._chunks)[:top_k]


def _pipeline(store, **overrides):
    p = RagPipeline.__new__(RagPipeline)
    p._store = store
    p._retriever = None
    p._source_provider = "slack"
    p._settings = RagSettings(**overrides) if overrides else RagSettings()
    return p


# -- the fix -----------------------------------------------------------------


def test_a_tagged_question_reads_every_chunk_in_scope():
    """13% was the bug. 100% is the fix."""
    chunks = [_chunk(f"d{i}", 0.4 + i / 100, day=i + 1) for i in range(38)]
    store = _Store(chunks)
    hits, gate = _pipeline(store)._whole_scope(
        "o", [0.1], workspace_id=None, tags=["channel:C1"], viewer=None
    )
    assert len(hits) == 38
    # ...and it asked for one MORE than the bound, so "too big" needs no COUNT.
    assert store.asked_top_k == [RagSettings().scope_whole_max_chunks + 1]


def test_the_whole_read_is_ordered_oldest_first():
    """A discussion is a narrative. Similarity order reads as unrelated
    fragments, which is the other half of why the answers felt thin."""
    chunks = [_chunk("new", 0.9, day=20), _chunk("old", 0.4, day=1), _chunk("mid", 0.8, day=10)]
    hits, _ = _pipeline(_Store(chunks))._whole_scope(
        "o", [0.1], workspace_id=None, tags=["channel:C1"], viewer=None
    )
    assert [h.document_id for h in hits] == ["old", "mid", "new"]


def test_the_gate_is_still_the_best_cosine():
    """The confidence gate must not change meaning: `gate_score` is the best
    cosine on both paths, or 0.35 stops being calibrated for anything."""
    chunks = [_chunk("a", 0.42, day=1), _chunk("b", 0.77, day=2)]
    _, gate = _pipeline(_Store(chunks))._whole_scope(
        "o", [0.1], workspace_id=None, tags=["t"], viewer=None
    )
    assert gate == 0.77


# -- it never runs PARTIALLY -------------------------------------------------


def test_too_many_chunks_falls_back_to_ranking():
    """Binary on purpose. A whole-scope read that quietly kept the newest N
    would reproduce the exact complaint on a busier channel, invisibly."""
    chunks = [_chunk(f"d{i}", 0.5, day=1) for i in range(200)]
    assert (
        _pipeline(_Store(chunks), scope_whole_max_chunks=120)._whole_scope(
            "o", [0.1], workspace_id=None, tags=["t"], viewer=None
        )
        is None
    )


def test_too_many_chars_falls_back_to_ranking():
    """The real constraint is prompt size, not chunk count -- a handful of long
    threads can exceed the budget that a hundred short ones fit inside."""
    chunks = [_chunk(f"d{i}", 0.5, day=1, content="y" * 5000) for i in range(30)]
    assert (
        _pipeline(_Store(chunks), scope_whole_max_chars=60000)._whole_scope(
            "o", [0.1], workspace_id=None, tags=["t"], viewer=None
        )
        is None
    )


def test_an_untagged_question_is_untouched():
    """Company-wide Ask keeps ranked retrieval: the corpus there is the whole
    company and was never a candidate for reading whole."""
    store = _Store([_chunk("a", 0.9, day=1)])
    assert _pipeline(store)._whole_scope(
        "o", [0.1], workspace_id=None, tags=None, viewer=None
    ) is None
    assert store.asked_top_k == []  # not even probed


def test_an_empty_scope_falls_back():
    assert _pipeline(_Store([]))._whole_scope(
        "o", [0.1], workspace_id=None, tags=["t"], viewer=None
    ) is None


def test_a_store_failure_never_costs_the_answer():
    """This is an optimisation. It must degrade to the behaviour that shipped."""

    class _Broken:
        def query(self, *a, **kw):
            raise RuntimeError("db down")

    assert _pipeline(_Broken())._whole_scope(
        "o", [0.1], workspace_id=None, tags=["t"], viewer=None
    ) is None


def test_the_viewer_filter_still_applies():
    """A whole-scope read is still an ACCESS-filtered read: it widens how much
    of what you may see reaches the prompt, never what you may see."""
    seen = {}

    class _Recording:
        def query(self, org_id, query_vec, **kw):
            seen.update(kw)
            return [_chunk("a", 0.5, day=1)]

    from app.vectorstore.base import Viewer

    viewer = Viewer(email="ada@x.com")
    _pipeline(_Recording())._whole_scope(
        "o", [0.1], workspace_id="w1", tags=["channel:C1"], viewer=viewer
    )
    assert seen["viewer"] is viewer
    assert seen["tags"] == ["channel:C1"]
    assert seen["workspace_id"] == "w1"
    assert seen["source_provider"] == "slack"


def test_an_undatable_chunk_is_kept_not_dropped():
    """Sorting must not shrink the corpus this path exists to deliver."""
    dated = _chunk("a", 0.5, day=2)
    undated = RetrievedChunk("c", 0.5, "b", 0, "o")
    assert [h.document_id for h in sorted([dated, undated], key=_read_order)] == ["b", "a"]
