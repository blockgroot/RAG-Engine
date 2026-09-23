"""The model reads a question's retrieval intent; this module never trusts it.

Validation is the gate, not the prompt: several tests assume the model LOST --
a chatty reply, an invented label, an impossible date -- and assert the outcome
is still the conservative behaviour that shipped. The pipeline tests pin that
the ONE intent drives both breadth and time, and that nothing downstream falls
back to a word list while the model is answering.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.config.settings import RagSettings
from app.rag.pipeline import RagPipeline
from app.rag.query_intent import (
    OVERVIEW,
    SPECIFIC,
    TIME_NONE,
    TIME_RANGE,
    TIME_RECENT,
    QueryIntent,
    classify_query_intent,
)
from app.vectorstore.base import RetrievedChunk

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class _Llm:
    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt, **kw):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _ask(reply, question="q"):
    return classify_query_intent(question, llm=_Llm(reply), now=NOW)


def _j(**kw):
    return json.dumps(kw)


# -- the model decides --------------------------------------------------------


def test_summarise_everything_is_an_overview_with_no_time_filter():
    """The user's case: "summarise all the goals and discussion in the channel"
    must read the whole channel, start to end, with no date narrowing it."""
    intent = _ask(_j(scope="overview", time="none", start=None, end=None))
    assert intent.overview
    assert intent.time == TIME_NONE and intent.window is None
    assert intent.recency is None
    assert intent.source == "model"


def test_a_named_period_becomes_a_lenient_window():
    intent = _ask(_j(scope="specific", time="range", start="2026-09-21", end="2026-09-23"))
    assert intent.time == TIME_RANGE
    assert intent.window.after == datetime(2026, 9, 20, tzinfo=timezone.utc)
    # An end of today means "until now": no upper bound to cut off the answer.
    assert intent.window.before is None
    assert intent.recency is not None and intent.recency.window == intent.window


def test_a_closed_past_period_keeps_its_upper_bound_with_slack():
    intent = _ask(_j(scope="specific", time="range", start="2026-03-01", end="2026-03-31"))
    assert intent.window.after == datetime(2026, 2, 28, tzinfo=timezone.utc)
    assert intent.window.before == datetime(2026, 4, 2, tzinfo=timezone.utc)


def test_latest_on_something_is_a_boost_not_a_filter():
    intent = _ask(_j(scope="specific", time="recent", start=None, end=None))
    assert intent.time == TIME_RECENT and intent.window is None
    assert intent.recency is not None and intent.recency.window is None


def test_a_phrase_no_word_list_knows_is_still_read():
    """The reason this is a model and not a regex: nothing in "since the
    offsite" is a time word, and the floor would have missed it entirely."""
    llm = _Llm(_j(scope="specific", time="range", start="2026-09-14", end=None))
    intent = classify_query_intent(
        "what did we agree since the offsite?", llm=llm, now=NOW
    )
    assert intent.time == TIME_RANGE
    # ...and the model was told what "today" is, or it could not date anything.
    assert "2026-09-23" in llm.prompts[0]


def test_a_fenced_or_prefixed_reply_is_still_parsed():
    reply = 'Sure:\n```json\n{"scope": "overview", "time": "none", "start": null, "end": null}\n```'
    assert _ask(reply).overview


# -- validation is the gate ---------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        _j(scope="everything", time="none"),
        _j(scope=None, time="none"),
        _j(time="none"),
    ],
)
def test_an_unknown_breadth_reads_as_specific(reply):
    """Widening on a maybe is the change with a cost."""
    assert _ask(reply).breadth == SPECIFIC


def test_an_unknown_time_label_reads_as_no_filter():
    assert _ask(_j(scope="specific", time="soonish")).time == TIME_NONE


@pytest.mark.parametrize(
    "start, end",
    [
        ("2026-10-10", None),           # starts in the future
        ("2026-09-20", "2026-09-01"),   # inverted
        ("next tuesday", None),         # not a date
        (None, "2026-09-01"),           # no start
    ],
)
def test_an_unusable_window_degrades_to_a_boost(start, end):
    """The model said "a period" but gave none we can use. A boost cannot
    exclude the answer; a wrong filter can."""
    intent = _ask(_j(scope="specific", time="range", start=start, end=end))
    assert intent.time == TIME_RECENT and intent.window is None


def test_a_bad_window_does_not_cost_the_breadth():
    intent = _ask(_j(scope="overview", time="range", start="garbage", end=None))
    assert intent.overview


# -- failing open -------------------------------------------------------------


@pytest.mark.parametrize("reply", ["", "overview", "I think this is broad", None])
def test_a_reply_that_is_not_json_uses_the_floor(reply):
    intent = _ask(reply, question="what is the leave policy?")
    assert intent == QueryIntent()


def test_a_dead_classifier_falls_back_to_the_floor():
    intent = _ask(RuntimeError("429"), question="what changed this week?")
    assert intent.source == "fallback"
    assert intent.time == TIME_RANGE and intent.window is not None
    # ...and never widens on an outage.
    assert intent.breadth == SPECIFIC


def test_no_budget_skips_the_call():
    llm = _Llm(_j(scope="overview", time="none"))
    intent = classify_query_intent("summarise everything", llm=llm, now=NOW, use_model=False)
    assert llm.prompts == []
    assert intent.source == "fallback" and not intent.overview


# -- the pipeline uses ONE intent for breadth and time ------------------------


class _Retriever:
    recency_enabled = True


class _Budget:
    def __init__(self, ok=True):
        self.ok = ok

    def can_spend(self, _seconds):
        return self.ok


def _pipeline(llm, retriever=_Retriever()):
    p = RagPipeline.__new__(RagPipeline)
    p._llm = llm
    p._retriever = retriever
    p._settings = RagSettings()
    p._source_provider = "slack"

    class _BudgetSettings:
        min_stage_seconds = 1.0

    p._budget_settings = _BudgetSettings()
    return p


def test_one_call_serves_breadth_and_time():
    llm = _Llm(_j(scope="overview", time="none"))
    intent = _pipeline(llm)._classify_intent(
        "summarise all the goals and discussion in the channel",
        workspace_id=None, tags=["channel:C1"], budget=_Budget(),
    )
    assert intent.overview and intent.recency is None
    assert len(llm.prompts) == 1


def test_nothing_to_decide_means_no_call():
    """Company-wide with recency switched off: neither breadth nor time is
    used downstream, so the classifier must not spend a request."""

    class _Off:
        recency_enabled = False

    llm = _Llm(_j(scope="overview", time="none"))
    assert _pipeline(llm, _Off())._classify_intent(
        "q", workspace_id=None, tags=None, budget=_Budget()
    ) is None
    assert llm.prompts == []


def test_time_is_dropped_when_the_retriever_cannot_act_on_it():
    class _Off:
        recency_enabled = False

    llm = _Llm(_j(scope="overview", time="recent"))
    intent = _pipeline(llm, _Off())._classify_intent(
        "q", workspace_id="w1", tags=None, budget=_Budget()
    )
    assert intent.overview and intent.recency is None


def test_the_whole_read_trusts_the_shared_intent():
    """`_whole_scope` must use the classified breadth rather than calling the
    old one-word classifier a second time."""
    llm = _Llm("specific")  # would say "rank" if it were consulted

    class _Store:
        def query(self, *a, **kw):
            return [RetrievedChunk("c", 0.5, "d", 0, "o")]

    p = _pipeline(llm)
    p._store = _Store()
    result = p._whole_scope(
        "o", "summarise it all", [0.1], workspace_id=None, tags=["t"], viewer=None,
        intent=QueryIntent(breadth=OVERVIEW, source="model"),
    )
    assert result is not None
    assert llm.prompts == []
