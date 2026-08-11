"""Question tone: classify intent, then compose empathy outside grounding.

Production shape (no keyword lists, no empathy rules in the grounded prompt):

1. ``classify`` — cheap aux LLM labels the QUESTION as ``factual`` or
   ``supportive`` (semantic rubric; paraphrases welcome; topic alone never
   forces SUPPORTIVE).
2. Grounded generate — policy facts only (unchanged gate + slim prompt).
3. ``compose`` — if SUPPORTIVE and we have an answer, a second tiny aux call
   writes one acknowledgment sentence; code prepends it to the grounded body.

Empathy is therefore a structural pipeline step, not something we hope the
large grounding prompt will remember.
"""

from __future__ import annotations

import re
from typing import Literal

QuestionTone = Literal["factual", "supportive"]

_LABEL_RE = re.compile(r"\b(FACTUAL|SUPPORTIVE)\b", re.IGNORECASE)


def build_question_tone_prompt(question: str) -> str:
    """Tiny aux classify prompt — one label, no answer content."""
    return (
        "Classify the USER MESSAGE. Reply with exactly one word: FACTUAL or SUPPORTIVE.\n\n"
        "FACTUAL = asking what a policy, benefit, process, or document says or how "
        "something works (topic may be sensitive; they want information).\n"
        "SUPPORTIVE = personally describing difficulty or asking what to do about "
        "how they feel (any wording). Do not pick SUPPORTIVE only because the "
        "topic is mental health or counselling.\n\n"
        "FACTUAL ex: What should I know from the Mental Health Policy?\n"
        "FACTUAL ex: How many counselling sessions are covered?\n"
        "SUPPORTIVE ex: Work has been crushing me — how do I cope?\n"
        "SUPPORTIVE ex: I am feeling very stressed, what should I do?\n\n"
        f"USER MESSAGE:\n{question.strip()}\n\n"
        "QUESTION_TONE_LABEL:"
    )


def parse_question_tone(raw: str) -> QuestionTone | None:
    """Extract ``factual`` / ``supportive`` from a classify response."""
    if not raw or not raw.strip():
        return None
    matches = _LABEL_RE.findall(raw)
    if not matches:
        return None
    return matches[-1].lower()  # type: ignore[return-value]


def build_empathy_opener_prompt(question: str) -> str:
    """Tiny aux prompt: one acknowledgment sentence only — no policy facts."""
    return (
        "Write exactly ONE short, warm sentence acknowledging the user's "
        "situation. Do not give advice, list steps, mention company policy, "
        "documents, forms, or benefits. Do not invent facts.\n\n"
        f"USER MESSAGE:\n{question.strip()}\n\n"
        "OPENER:"
    )


def normalize_opener(raw: str) -> str | None:
    """Take the first non-empty line of an opener generation; empty → None."""
    if not raw:
        return None
    for line in raw.strip().splitlines():
        line = line.strip().strip('"').strip("'")
        if line:
            if line.upper().startswith("OPENER:"):
                line = line.split(":", 1)[-1].strip()
            if line:
                return line
    return None


def compose_supportive_answer(opener: str, grounded_body: str) -> str:
    """Prepend a classified supportive opener to grounded policy text."""
    body = grounded_body.strip()
    head = opener.strip()
    if not head:
        return body
    if not body:
        return head
    return f"{head}\n\n{body}"
