"""Semantic question-tone classification for empathy vs factual replies.

Keyword lists cannot cover how users describe distress ("work is crushing me",
"I can't cope", paraphrases, indirect asks). Production approach: a cheap aux
LLM classifies the *question intent* as ``factual`` or ``supportive`` using a
rubric + contrastive examples, then the grounded prompt and tone-compliance
retry enforce that decision. Topic alone (e.g. "Mental Health Policy") must
never force sympathy.
"""

from __future__ import annotations

import re
from typing import Literal

QuestionTone = Literal["factual", "supportive"]

_LABEL_RE = re.compile(r"\b(FACTUAL|SUPPORTIVE)\b", re.IGNORECASE)


def build_question_tone_prompt(question: str) -> str:
    """Prompt for a tiny aux classify call — label only, no answer content."""
    return (
        "Classify the USER MESSAGE for how a company handbook assistant should reply.\n\n"
        "Choose exactly one label:\n"
        "- FACTUAL — the user is asking what a policy, benefit, process, or document "
        "says or contains, or how something works. The topic may be sensitive "
        "(mental health, leave, counselling) but they are seeking information, "
        "not describing personal distress.\n"
        "- SUPPORTIVE — the user is personally describing difficulty, distress, "
        "or emotional struggle, OR asking what they should do about how they feel. "
        "Paraphrase, slang, and indirect wording still count as SUPPORTIVE. "
        "Do NOT choose SUPPORTIVE merely because the topic involves wellbeing "
        "or mental health.\n\n"
        "Examples:\n"
        "USER: What should I know from the Mental Health Policy?\n"
        "LABEL: FACTUAL\n\n"
        "USER: How many counselling sessions does the company cover?\n"
        "LABEL: FACTUAL\n\n"
        "USER: I am feeling very stressed lately, what should I do?\n"
        "LABEL: SUPPORTIVE\n\n"
        "USER: Work has been crushing me and I don't know how to cope — any help?\n"
        "LABEL: SUPPORTIVE\n\n"
        "USER: What's the remote work policy?\n"
        "LABEL: FACTUAL\n\n"
        f"USER MESSAGE:\n{question.strip()}\n\n"
        "Reply with exactly one word: FACTUAL or SUPPORTIVE.\n"
        "QUESTION_TONE_LABEL:"
    )


def parse_question_tone(raw: str) -> QuestionTone | None:
    """Extract ``factual`` / ``supportive`` from a classify response, if present."""
    if not raw or not raw.strip():
        return None
    matches = _LABEL_RE.findall(raw)
    if not matches:
        return None
    # Prefer the last explicit label (models sometimes echo the rubric first).
    return matches[-1].lower()  # type: ignore[return-value]


# Assistant-output shape checks (not user vocabulary). Used only after the
# semantic classifier has decided SUPPORTIVE vs FACTUAL.

_COLD_PROCEDURAL_OPENING_RE = re.compile(
    r"^\s*(?:"
    r"to (?:access|request|get|apply|submit|schedule)|"
    r"please (?:follow|fill|submit|complete)|"
    r"follow these steps|"
    r"fill out|"
    r"here (?:are|is) (?:how|the steps|what)|"
    r"you (?:can|may) (?:access|request|apply)"
    r")\b",
    re.IGNORECASE,
)

_UNWARRANTED_SYMPATHY_OPENING_RE = re.compile(
    r"^\s*(?:"
    r"i(?:'m| am) sorry you|"
    r"sorry (?:to hear )?(?:you(?:'ve| have) been feeling|you're feeling|you are feeling)|"
    r"it sounds like you (?:are|'re) (?:going through|feeling)|"
    r"sounds like you are going through"
    r")",
    re.IGNORECASE,
)

_WARM_OPENING_CUES = (
    "sorry",
    "hear you",
    "hear that",
    "sounds like",
    "sounds really",
    "that sounds",
    "tough",
    "hard",
    "difficult",
    "understand",
    "going through",
    "must be",
    "appreciate you",
    "glad you",
    "thank you for sharing",
    "here for you",
    "worth addressing",
    "crushing",
    "cope",
)


def lacks_supportive_opening(text: str) -> bool:
    """True when a SUPPORTIVE answer jumps into procedures with no warm lead-in."""
    opening = text.strip()
    if not opening:
        return True
    if _COLD_PROCEDURAL_OPENING_RE.match(opening):
        return True
    head = opening[:280].lower()
    return not any(cue in head for cue in _WARM_OPENING_CUES)


def has_unwarranted_sympathy_opening(text: str) -> bool:
    """True when a FACTUAL answer opens as if the user personally disclosed distress."""
    return _UNWARRANTED_SYMPATHY_OPENING_RE.match(text.strip()) is not None
