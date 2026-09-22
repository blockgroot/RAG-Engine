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


class _Llm:
    """Stands in for the scope-intent classifier."""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    def generate(self, prompt, **kw):
        self.calls += 1
        return self.reply


def _pipeline(store, intent="overview", **overrides):
    p = RagPipeline.__new__(RagPipeline)
    p._store = store
    p._retriever = None
    p._source_provider = "slack"
    p._settings = RagSettings(**overrides) if overrides else RagSettings()
    p._llm = _Llm(intent)
    return p


# -- the fix -----------------------------------------------------------------


def test_a_tagged_question_reads_every_chunk_in_scope():
    """13% was the bug. 100% is the fix."""
    chunks = [_chunk(f"d{i}", 0.4 + i / 100, day=i + 1) for i in range(38)]
    store = _Store(chunks)
    hits, gate = _pipeline(store)._whole_scope(
        "o", "q", [0.1], workspace_id=None, tags=["channel:C1"], viewer=None
    )
    assert len(hits) == 38
    # ...and it asked for one MORE than the bound, so "too big" needs no COUNT.
    assert store.asked_top_k == [RagSettings().scope_whole_max_chunks + 1]


def test_the_whole_read_is_ordered_oldest_first():
    """A discussion is a narrative. Similarity order reads as unrelated
    fragments, which is the other half of why the answers felt thin."""
    chunks = [_chunk("new", 0.9, day=20), _chunk("old", 0.4, day=1), _chunk("mid", 0.8, day=10)]
    hits, _ = _pipeline(_Store(chunks))._whole_scope(
        "o", "q", [0.1], workspace_id=None, tags=["channel:C1"], viewer=None
    )
    assert [h.document_id for h in hits] == ["old", "mid", "new"]


def test_the_gate_is_still_the_best_cosine():
    """The confidence gate must not change meaning: `gate_score` is the best
    cosine on both paths, or 0.35 stops being calibrated for anything."""
    chunks = [_chunk("a", 0.42, day=1), _chunk("b", 0.77, day=2)]
    _, gate = _pipeline(_Store(chunks))._whole_scope(
        "o", "q", [0.1], workspace_id=None, tags=["t"], viewer=None
    )
    assert gate == 0.77


# -- it never runs PARTIALLY -------------------------------------------------


def test_over_budget_gives_as_much_as_fits_newest_first():
    """They asked for all of it, so give as much of it as a prompt can hold --
    NOT a silent drop back to five chunks. An overview that has to cut
    something cuts the oldest, then reads oldest-first."""
    chunks = [_chunk(f"d{i}", 0.5, day=i, content="y" * 5000) for i in range(30)]
    hits, _ = _pipeline(_Store(chunks), scope_whole_max_chars=60000)._whole_scope(
        "o", "q", [0.1], workspace_id=None, tags=["t"], viewer=None
    )
    assert 0 < len(hits) < 30                      # bounded
    assert len(hits) == 12                         # 60000 / 5000
    kept = {h.document_id for h in hits}
    assert "d29" in kept and "d0" not in kept      # newest survive, oldest cut
    # ...and what survives is still handed over oldest-first, for reading.
    assert [h.document_id for h in hits] == sorted(kept, key=lambda d: int(d[1:]))


def test_the_chunk_bound_also_applies():
    """Two bounds, because a hundred short threads and three long ones fail for
    different reasons."""
    chunks = [_chunk(f"d{i}", 0.5, day=i, content="z" * 10) for i in range(400)]
    hits, _ = _pipeline(_Store(chunks), scope_whole_max_chunks=120)._whole_scope(
        "o", "q", [0.1], workspace_id=None, tags=["t"], viewer=None
    )
    assert len(hits) == 120


def test_an_untagged_question_is_untouched():
    """Company-wide Ask keeps ranked retrieval: the corpus there is the whole
    company and was never a candidate for reading whole."""
    store = _Store([_chunk("a", 0.9, day=1)])
    assert _pipeline(store)._whole_scope(
        "o", "q", [0.1], workspace_id=None, tags=None, viewer=None
    ) is None
    assert store.asked_top_k == []  # not even probed


def test_an_empty_scope_falls_back():
    assert _pipeline(_Store([]))._whole_scope(
        "o", "q", [0.1], workspace_id=None, tags=["t"], viewer=None
    ) is None


def test_a_store_failure_never_costs_the_answer():
    """This is an optimisation. It must degrade to the behaviour that shipped."""

    class _Broken:
        def query(self, *a, **kw):
            raise RuntimeError("db down")

    assert _pipeline(_Broken())._whole_scope(
        "o", "q", [0.1], workspace_id=None, tags=["t"], viewer=None
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
        "o", "q", [0.1], workspace_id="w1", tags=["channel:C1"], viewer=viewer
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


# -- the whole read must survive context assembly ----------------------------


def test_a_whole_read_is_not_trimmed_back_to_a_fragment():
    """The bug this change nearly introduced. `max_context_chars` is 6000,
    sized for five chunks, and `assemble_context_texts` keeps a PREFIX -- so a
    time-ordered whole read would have kept the OLDEST 6k and dropped every
    recent thread. That is the original complaint inverted, and worse: it reads
    as the bot forgetting this week."""
    from app.rag.context_assemble import assemble_context_texts, describe_hit

    hits = [_chunk(f"d{i}", 0.5, day=i, content="x" * 700) for i in range(38)]
    texts = [describe_hit(h) for h in hits]
    settings = RagSettings()

    # What the OLD budget would have done to it.
    trimmed = assemble_context_texts(texts, settings.max_context_chars)
    assert len(trimmed) < len(hits)          # would have dropped most of them
    assert trimmed[0] == texts[0]            # ...keeping the OLDEST
    assert texts[-1] not in trimmed          # ...and losing the newest

    # A whole read carries no second budget: `_whole_scope` already enforced
    # one on the same corpus before deciding to read it whole.
    assert assemble_context_texts(texts, 0) == texts


def test_a_normal_retrieval_still_honours_the_context_budget():
    """The 6k budget is right for five chunks and must not be widened for
    everything just because one path needed more."""
    from app.rag.context_assemble import assemble_context_texts, describe_hit

    hits = [_chunk(f"d{i}", 0.5, day=i, content="x" * 4000) for i in range(5)]
    texts = [describe_hit(h) for h in hits]
    kept = assemble_context_texts(texts, RagSettings().max_context_chars)
    assert 0 < len(kept) < len(texts)


def test_only_the_whole_read_can_exceed_top_k():
    """The invariant `_generate` infers the whole read from. Every ranked path
    caps at `top_k`, so more than that can only have come from `_whole_scope`."""
    settings = RagSettings()
    chunks = [_chunk(f"d{i}", 0.5, day=i) for i in range(38)]
    store = _Store(chunks)
    hits, _ = _pipeline(store)._whole_scope(
        "o", "q", [0.1], workspace_id=None, tags=["t"], viewer=None
    )
    assert len(hits) > settings.top_k


# -- the trigger is the QUESTION ---------------------------------------------


def test_a_specific_question_never_widens():
    """The whole point of classifying. "What did Sana say about Notion?" is a
    pointed question wearing no summary verb at all, and handing it 38 chunks
    is not an improvement -- it is a different question."""
    store = _Store([_chunk(f"d{i}", 0.5, day=i) for i in range(38)])
    assert _pipeline(store, intent="specific")._whole_scope(
        "o", "what did sana say about notion", [0.1],
        workspace_id=None, tags=["t"], viewer=None,
    ) is None
    # ...and it did not pay for a breadth query it would not use.
    assert store.asked_top_k == []


def test_a_summary_verb_about_ONE_topic_is_still_specific():
    """The case a word list cannot get right: "summarise" appears, but the
    question names a single topic. The classifier decides, not the wording."""
    store = _Store([_chunk("d1", 0.5, day=1)])
    assert _pipeline(store, intent="specific")._whole_scope(
        "o", "summarise what sana said about notion", [0.1],
        workspace_id=None, tags=["t"], viewer=None,
    ) is None


def test_an_overview_question_widens():
    store = _Store([_chunk(f"d{i}", 0.5, day=i) for i in range(38)])
    hits, _ = _pipeline(store, intent="overview")._whole_scope(
        "o", "summarise the overall discussion in the channel", [0.1],
        workspace_id=None, tags=["t"], viewer=None,
    )
    assert len(hits) == 38


def test_an_unscoped_question_is_never_classified():
    """Company-wide Ask reaches everything the org has, where "all of it" is
    neither affordable nor what anyone means -- so it does not even pay for the
    classifier call."""
    store = _Store([_chunk("d1", 0.9, day=1)])
    pipe = _pipeline(store, intent="overview")
    assert pipe._whole_scope(
        "o", "summarise everything", [0.1], workspace_id=None, tags=None, viewer=None
    ) is None
    assert pipe._llm.calls == 0
    assert store.asked_top_k == []


def test_a_dead_classifier_falls_back_to_ranking():
    """`fail_open`: a breadth read is an improvement on a narrow one, never a
    correctness guarantee, so an outage must not be able to take an answer."""

    class _Broken:
        def generate(self, *a, **kw):
            raise RuntimeError("429")

    pipe = _pipeline(_Store([_chunk("d1", 0.5, day=1)]))
    pipe._llm = _Broken()
    assert pipe._whole_scope(
        "o", "summarise everything", [0.1], workspace_id=None, tags=["t"], viewer=None
    ) is None


def test_a_space_scoped_question_is_a_candidate_without_tags():
    """A space is a scope too: its corpus is bounded by membership, so an
    overview of it is a real request even with no channel tag."""
    store = _Store([_chunk(f"d{i}", 0.5, day=i) for i in range(5)])
    hits, _ = _pipeline(store, intent="overview")._whole_scope(
        "o", "what is this space about", [0.1],
        workspace_id="w1", tags=None, viewer=None,
    )
    assert len(hits) == 5
