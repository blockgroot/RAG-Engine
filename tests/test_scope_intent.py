"""The classifier that decides whether a question needs breadth or precision.

Validation is the gate, not the prompt: a test assumes the model LOST and
asserts the outcome is still the ranked behaviour that shipped.
"""

from __future__ import annotations

import pytest

from app.rag.scope_intent import OVERVIEW, SPECIFIC, classify_scope_intent


class _Llm:
    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.reply


@pytest.mark.parametrize("reply", ["overview", "Overview", " overview.", "Answer: overview"])
def test_an_overview_reply_is_read_in_any_shape(reply):
    """A small model routinely answers "overview." or "Answer: overview"."""
    assert classify_scope_intent("summarise the channel", llm=_Llm(reply)) == OVERVIEW


@pytest.mark.parametrize("reply", ["specific", "SPECIFIC", "specific - one topic"])
def test_a_specific_reply_is_read_in_any_shape(reply):
    assert classify_scope_intent("what is the leave policy", llm=_Llm(reply)) == SPECIFIC


@pytest.mark.parametrize("reply", ["", "banana", "I think it depends", "chart", None])
def test_anything_outside_the_closed_set_reads_as_specific(reply):
    """Assume the prompt LOST. A hallucinated or chatty reply must cost the
    ranked behaviour that shipped, never an answer."""
    assert classify_scope_intent("q", llm=_Llm(reply)) == SPECIFIC


def test_a_reply_naming_BOTH_loses_the_tie_to_specific():
    """Widening on a maybe is the change with a cost, so ambiguity keeps the
    narrow read rather than letting dict ordering decide."""
    assert classify_scope_intent("q", llm=_Llm("specific or overview")) == SPECIFIC


def test_a_dead_classifier_fails_open():
    class _Broken:
        def generate(self, *a, **kw):
            raise RuntimeError("429")

    assert classify_scope_intent("summarise everything", llm=_Broken()) == SPECIFIC


def test_fail_open_false_raises_for_a_caller_that_wants_to_know():
    class _Broken:
        def generate(self, *a, **kw):
            raise RuntimeError("429")

    with pytest.raises(RuntimeError):
        classify_scope_intent("q", llm=_Broken(), fail_open=False)


def test_an_empty_question_needs_no_call():
    llm = _Llm("overview")
    assert classify_scope_intent("   ", llm=llm) == SPECIFIC
    assert llm.prompts == []


def test_the_question_reaches_the_prompt():
    llm = _Llm("overview")
    classify_scope_intent("what have we been discussing", llm=llm)
    assert "what have we been discussing" in llm.prompts[0]
