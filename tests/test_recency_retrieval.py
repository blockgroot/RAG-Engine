"""Recency-aware retrieval: "what happened recently?" reads what is RECENT.

The defect: retrieval ranked by similarity alone, so an older, wordier thread
outranked yesterday's message and the answer read as stale even though the new
content was indexed. Three layers are pinned here, all without a database or a
model -- the store is a fake that records what it was asked:

1. ``detect_recency`` -- the outage FLOOR (the model reads intent first, see
   ``test_query_intent.py``): which phrases it acts on and which it must not;
2. ``HybridRetriever`` -- the recent leg and newest-first fusion reorder, and
   never move the confidence gate;
3. ``RagPipeline._retrieve_in_window`` -- an explicit window is a hard filter
   that widens instead of refusing when it matches nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import RagSettings, RetrievalSettings
from app.rag.pipeline import RagPipeline
from app.rag.query_intent import TIME_RANGE, TIME_RECENT, QueryIntent
from app.rag.recency_intent import RecencyIntent, detect_recency
from app.rag.retrieval import HybridRetriever
from app.vectorstore.base import DateRange, RetrievedChunk

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _days_back(intent: RecencyIntent) -> float:
    assert intent.window is not None and intent.window.after is not None
    return (NOW - intent.window.after) / timedelta(days=1)


# -- 1. the outage floor ------------------------------------------------------


@pytest.mark.parametrize(
    "question, days",
    [
        ("What was discussed today?", 2),
        ("what happened yesterday in #eng", 3),
        ("What changed this week?", 8),
        ("Summarise last week's decisions", 15),        # the PREVIOUS week
        ("any blockers in the past 3 days?", 4),
        ("PRs merged in the last two weeks", 15),
        ("what did we ship over the last few months", 94),
        ("updates from the previous 10 days", 11),
        ("what did finance decide this month", 32),
    ],
)
def test_an_explicit_window_is_a_hard_filter_with_a_day_of_slack(question, days):
    intent = detect_recency(question, now=NOW)
    assert intent is not None and intent.window is not None
    assert _days_back(intent) == pytest.approx(days)


@pytest.mark.parametrize(
    "question",
    [
        "What was discussed recently?",
        "What's the latest on the Notion migration?",
        "anything new in the design channel",
        "Any updates on ENG-12?",
        "what have people been talking about lately",
    ],
)
def test_a_vague_ask_boosts_but_never_filters(question):
    """"The latest leave policy" still wants a policy edited a year ago --
    excluding old documents would turn a right answer into a refusal."""
    intent = detect_recency(question, now=NOW)
    assert intent is not None
    assert intent.window is None


@pytest.mark.parametrize(
    "question",
    [
        "What is our leave policy?",
        "How does onboarding work for a new joiner?",   # bare "new"
        "What is the last day to submit expenses?",     # a deadline, not a window
        "What happens in the last week of the quarter?",  # a period inside another
        "Where do I update my last name?",              # bare "last"
        "Who reviewed the auth PR?",
        "",
    ],
)
def test_words_that_only_look_temporal_do_not_trigger(question):
    assert detect_recency(question, now=NOW) is None


def test_the_most_specific_expression_wins():
    """A counted window beats a vague word, so the filter is the tight one."""
    intent = detect_recency("latest updates from the past 3 days", now=NOW)
    assert intent is not None and intent.window is not None
    assert _days_back(intent) == pytest.approx(4)


# -- 2. the retriever ---------------------------------------------------------


def _chunk(doc: str, score: float, days_ago: int | None) -> RetrievedChunk:
    return RetrievedChunk(
        content=f"content of {doc}",
        score=score,
        document_id=doc,
        chunk_index=0,
        org_id="o",
        last_modified=(
            None if days_ago is None else datetime.now(timezone.utc) - timedelta(days=days_ago)
        ),
    )


class _Store:
    """Answers vector queries by similarity, honouring ``date_range.after``,
    and records every range it was asked for."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.vector_ranges: list[DateRange | None] = []

    def query(self, org_id, vec, *, top_k=5, date_range=None, **kw):
        self.vector_ranges.append(date_range)
        hits = [
            c for c in self.chunks
            if date_range is None
            or date_range.after is None
            or (c.last_modified is not None and c.last_modified >= date_range.after)
        ]
        return sorted(hits, key=lambda c: c.score, reverse=True)[:top_k]

    def keyword_search(self, *a, **kw):
        return []


def _retriever(store, **settings):
    return HybridRetriever(
        store,
        reranker=None,
        settings=RetrievalSettings(rerank_enabled=False, **settings),
        rag_settings=RagSettings(top_k=2),
    )


def _corpus():
    # The old, wordy thread out-scores yesterday's short message on cosine:
    # exactly the live failure.
    return [
        _chunk("old-wordy", 0.72, days_ago=200),
        _chunk("older", 0.70, days_ago=300),
        _chunk("yesterday", 0.55, days_ago=1),
    ]


def test_without_recency_nothing_changes():
    """No intent means no extra leg and the similarity order that shipped."""
    store = _Store(_corpus())
    result = _retriever(store).retrieve("o", "q", [0.1])
    assert [h.document_id for h in result.hits] == ["old-wordy", "older"]
    assert store.vector_ranges == [None]


def test_a_recency_ask_promotes_the_recent_chunk():
    store = _Store(_corpus())
    result = _retriever(store).retrieve(
        "o", "q", [0.1], recency=RecencyIntent(None, "recently")
    )
    ids = [h.document_id for h in result.hits]
    assert ids[0] == "yesterday"
    # ...by ADDING a leg over the default window, never replacing the main one.
    assert store.vector_ranges[0] is None
    recent = store.vector_ranges[1]
    assert recent is not None and recent.after is not None
    assert (datetime.now(timezone.utc) - recent.after) / timedelta(days=1) == pytest.approx(
        RetrievalSettings().recency_default_days, abs=0.01
    )


def test_old_documents_are_reordered_not_excluded():
    """A vague ask never filters: with nothing recent in scope, the old
    documents still answer."""
    store = _Store([_chunk("old-wordy", 0.72, 200), _chunk("older", 0.70, 300)])
    result = _retriever(store).retrieve(
        "o", "q", [0.1], recency=RecencyIntent(None, "latest")
    )
    assert {h.document_id for h in result.hits} == {"old-wordy", "older"}


def test_the_gate_is_still_the_best_cosine():
    """0.35 is calibrated for cosine; recency reorders and must not move it."""
    store = _Store(_corpus())
    plain = _retriever(store).retrieve("o", "q", [0.1])
    boosted = _retriever(_Store(_corpus())).retrieve(
        "o", "q", [0.1], recency=RecencyIntent(None, "recently")
    )
    assert boosted.gate_score == plain.gate_score == 0.72


def test_the_recent_leg_never_searches_outside_a_hard_filter():
    """A leg wider than the caller's range would smuggle excluded documents
    back in through the fusion, so the two windows are intersected."""
    store = _Store(_corpus())
    caller = DateRange(after=datetime.now(timezone.utc) - timedelta(days=5))
    _retriever(store).retrieve(
        "o", "q", [0.1], date_range=caller, recency=RecencyIntent(None, "recently")
    )
    recent = store.vector_ranges[1]
    assert recent is not None and recent.after == caller.after


def test_undated_chunks_sort_last_not_first():
    store = _Store([_chunk("undated", 0.9, None), _chunk("fresh", 0.5, 1)])
    result = _retriever(store).retrieve(
        "o", "q", [0.1], recency=RecencyIntent(None, "recently")
    )
    assert result.hits[0].document_id == "fresh"


def test_the_switch_turns_it_off():
    store = _Store(_corpus())
    result = _retriever(store, recency_enabled=False).retrieve(
        "o", "q", [0.1], recency=RecencyIntent(None, "recently")
    )
    assert [h.document_id for h in result.hits] == ["old-wordy", "older"]
    assert store.vector_ranges == [None]


# -- 3. the pipeline's window -------------------------------------------------


def _range(window):
    return QueryIntent(time=TIME_RANGE, window=window, source="model")


def _pipeline(results):
    """A pipeline whose sub-question retrieval is replaced by a recorder."""
    p = RagPipeline.__new__(RagPipeline)
    calls: list[DateRange | None] = []

    def fake(org_id, question, subs, *, date_range=None, **kw):
        calls.append(date_range)
        return results.pop(0)

    p._retrieve_for_subquestions = fake
    return p, calls


_KW = dict(workspace_id=None, tags=None, viewer=None, known_vectors={})


def test_an_explicit_window_becomes_the_retrieval_range():
    hit = _chunk("fresh", 0.6, 1)
    p, calls = _pipeline([([hit], 0.6)])
    window = DateRange(after=NOW - timedelta(days=8))
    hits, gate, used = p._retrieve_in_window(
        "o", "q", ["q"], date_range=None, intent=_range(window), **_KW
    )
    assert calls == [window]
    assert used == window and hits == [hit] and gate == 0.6


def test_an_empty_window_widens_instead_of_refusing():
    """"Nothing this week" is an answer the model can give from dated chunks;
    the bare fallback is not."""
    old = _chunk("old", 0.6, 90)
    p, calls = _pipeline([([], None), ([old], 0.6)])
    window = DateRange(after=NOW - timedelta(days=8))
    hits, _, used = p._retrieve_in_window(
        "o", "q", ["q"], date_range=None, intent=_range(window), **_KW
    )
    assert calls == [window, None]
    assert used is None and hits == [old]


def test_a_callers_range_is_never_overridden():
    caller = DateRange(after=NOW - timedelta(days=400))
    p, calls = _pipeline([([], None)])
    _, _, used = p._retrieve_in_window(
        "o", "q", ["q"],
        date_range=caller,
        intent=_range(DateRange(after=NOW - timedelta(days=8))),
        **_KW,
    )
    assert calls == [caller]      # one call, no widening past a hard filter
    assert used is caller


def test_a_vague_ask_does_not_filter_at_the_pipeline():
    p, calls = _pipeline([([_chunk("a", 0.5, 3)], 0.5)])
    p._retrieve_in_window(
        "o", "q", ["q"], date_range=None,
        intent=QueryIntent(time=TIME_RECENT, source="model"), **_KW
    )
    assert calls == [None]
