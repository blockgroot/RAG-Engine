"""The summary sent to a colleague: the conversation is fenced as outside text
(earlier answers repeat documents), bounded, and the reply keeps no [n] marker
and no link the conversation did not contain."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.askfriend.summary import MAX_ANSWER_CHARS, MAX_TURNS, build_summary_prompt, summarize
from app.core.exceptions import ProviderError
from app.memory.conversations import ConversationTurnRow
from app.security.untrusted import UNTRUSTED_POLICY, UNTRUSTED_REMINDER


class FakeLLM:
    def __init__(self, reply: str = "Rahul asked about X; Handbook found nothing.") -> None:
        self.reply = reply
        self.prompt = ""
        self.max_tokens = None

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.prompt, self.max_tokens = prompt, max_tokens
        return self.reply


def _row(q: str, a: str, i: int = 0) -> ConversationTurnRow:
    return ConversationTurnRow(i, q, a, datetime.now(timezone.utc), [], None, None)


def test_prompt_fences_conversation_with_policy():
    p = build_summary_prompt([("what is X?", "I couldn't find that.")], "what is X?", left_out=0)
    assert p.index(UNTRUSTED_POLICY) < p.index("what is X?") < p.index(UNTRUSTED_REMINDER)


def test_prompt_says_older_turns_left_out():
    assert "3 earlier" in build_summary_prompt([("q", "a")], "q", left_out=3)


def test_summarize_bounds_turns_and_answers():
    assert MAX_TURNS == 12 and MAX_ANSWER_CHARS == 2000
    fake = FakeLLM()
    turns = [_row(f"question-{i:02d}", "a" * 5000, i) for i in range(20)]
    summarize(turns, "question-19", fake)
    assert "question-07" not in fake.prompt and "question-08" in fake.prompt
    assert "a" * 2001 not in fake.prompt
    assert "8 earlier" in fake.prompt


def test_summarize_strips_markers_and_unseen_links():
    fake = FakeLLM("X is a repo [1]. See https://evil.example/x and https://github.com/o/r")
    out = summarize([_row("q", "see https://github.com/o/r")], "q", fake)
    assert "[1]" not in out and "evil.example" not in out and "https://github.com/o/r" in out


def test_summary_ends_with_the_exact_question():
    out = summarize([_row("What does bench-sandbox do?", "I couldn't find that.")],
                    "What does bench-sandbox do?", FakeLLM())
    assert out.rstrip().endswith("Question: What does bench-sandbox do?")


def test_reply_cap_fits_a_medium_summary():
    fake = FakeLLM()
    summarize([_row("q", "a")], "q", fake)
    assert fake.max_tokens == 1500


def test_empty_reply_raises():
    with pytest.raises(ProviderError):
        summarize([_row("q", "a")], "q", FakeLLM("  "))
