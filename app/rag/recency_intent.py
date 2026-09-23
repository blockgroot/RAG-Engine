"""The deterministic FLOOR for a question's time intent.

The primary reader of time intent is the model (`query_intent.classify_query_intent`),
because intent is not a word list: "the latest leave policy" asks for a policy
while "the latest on deploys" asks for what is new, and every phrasing a list
does not contain is a silent miss. This module answers only when that call
cannot -- a dead or rate-limited classifier, or a request with no budget left
-- the same posture the routing keyword rules take under `classify_question`.

As a floor it is deliberately narrow. Time expressions like "this week" or
"past 3 days" are unambiguous enough to act on without a model; the words that
are not ("new", "last") only count with a time unit after them, so an outage
degrades to ranked retrieval rather than to a wrong filter.

Two outcomes, and the split decides how hard retrieval leans on the date:

* an EXPLICIT window ("this week", "past 10 days") becomes a hard date filter,
  exactly like a caller-supplied `DateRange`;
* a VAGUE ask ("recently", "latest") only BOOSTS recent documents -- "the
  latest leave policy" still wants the policy even if it was last edited a
  year ago, so excluding old documents would turn a correct answer into a
  refusal.

Windows are deliberately LENIENT (a day of slack, "last week" reaching back two
weeks): we do not know the asker's timezone, and the two errors are not equal.
A window slightly too wide hands the model a few extra chunks, each still
labelled with its date by `describe_hit`; a window too narrow drops the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..vectorstore.base import DateRange


@dataclass(frozen=True)
class RecencyIntent:
    """What the question said about time.

    ``window`` is a hard filter for an explicit period and ``None`` for a vague
    recency ask, which only boosts. ``phrase`` is the matched text, kept for
    logs so a misfire can be traced to the words that caused it.
    """

    window: DateRange | None
    phrase: str


_UNIT_DAYS = {"day": 1, "week": 7, "month": 31, "quarter": 92, "year": 366}

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "few": 3,
    "couple of": 2, "couple": 2,
}

# "past 3 days", "last two weeks", "in the last few months", "previous 10 days"
_COUNTED = re.compile(
    r"\b(?:past|last|previous)\s+"
    r"(?P<n>\d{1,3}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|few|couple(?:\s+of)?)\s+"
    r"(?P<unit>day|week|month|quarter|year)s?\b(?!'?s?\s+of\b)"
)
# "past week", "last month", "this quarter", "previous week". No "day": "the
# last day to submit expenses" is a deadline, not a window. And never followed
# by "of" -- "the last week of the quarter" names a period inside something
# else, not a span ending now.
_SINGLE = re.compile(
    r"\b(?P<which>this|past|last|previous)\s+(?P<unit>week|month|quarter|year)\b(?!\s+of\b)"
)
_TODAY = re.compile(r"\b(?:today|tonight|this morning|this afternoon)\b")
_YESTERDAY = re.compile(r"\byesterday\b")
# Vague recency: boost, never filter. Bare "new" and bare "last" are excluded
# on purpose -- see the module docstring.
_VAGUE = re.compile(
    r"\b(?:recent|recently|lately|latest|newest|most recent|"
    r"what'?s new|whats new|anything new|any updates?|new updates?|"
    r"these days|of late|up to date)\b"
)


def _count(raw: str) -> int:
    raw = re.sub(r"\s+", " ", raw.strip())
    if raw.isdigit():
        return max(1, int(raw))
    return _NUMBER_WORDS.get(raw, 1)


def detect_recency(question: str, *, now: datetime | None = None) -> RecencyIntent | None:
    """Return the question's recency intent, or ``None`` when it has none.

    The most specific expression wins: a counted window beats a named one, and
    any explicit window beats a vague word, so "latest updates from the past
    3 days" filters to 3 days rather than merely boosting.
    """
    text = (question or "").lower()
    if not text.strip():
        return None
    now = now or datetime.now(timezone.utc)

    def since(days: float, phrase: str) -> RecencyIntent:
        # One day of slack on every window: we do not know the asker's
        # timezone, and a too-narrow window is the error that loses answers.
        return RecencyIntent(DateRange(after=now - timedelta(days=days + 1)), phrase)

    m = _COUNTED.search(text)
    if m:
        days = _count(m.group("n")) * _UNIT_DAYS[m.group("unit")]
        return since(days, m.group(0))

    m = _SINGLE.search(text)
    if m:
        unit_days = _UNIT_DAYS[m.group("unit")]
        # "this week" is the week so far; "last week" usually means the
        # PREVIOUS calendar week, so it has to reach back two.
        days = unit_days if m.group("which") in ("this", "past") else unit_days * 2
        return since(days, m.group(0))

    m = _YESTERDAY.search(text)
    if m:
        return since(2, m.group(0))

    m = _TODAY.search(text)
    if m:
        return since(1, m.group(0))

    m = _VAGUE.search(text)
    if m:
        return RecencyIntent(None, m.group(0))

    return None
