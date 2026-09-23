"""What does this question need from retrieval? Decided by the model, validated here.

Two properties of a question decide WHICH chunks can answer it, and neither is
visible to similarity search:

* **breadth** -- does the answer live in a few documents ("what did we decide
  about Notion?") or in the collection AS A WHOLE ("summarise all the goals
  and discussion in this channel")?
* **time** -- does it ask about a period ("what changed this week?"), about
  what is newest ("what's the latest on deploys?"), or not about time at all
  ("summarise everything from the start")?

Both used to be guessed differently: breadth by a one-word classifier, time by
a word list (`recency_intent`). A word list is the wrong tool for intent -- it
misses every phrasing nobody wrote down, and "latest" means something different
in "the latest leave policy" than in "the latest on deploys". So ONE small call
now reads the question and returns both, and this module never trusts it:

* the model picks LABELS from closed sets and at most two dates -- never the
  chunks, the query, the count or the SQL;
* every field is validated, and anything outside the closed set, a malformed
  date, an inverted or future window reads as the conservative value
  (`specific`, no time filter) rather than being passed on;
* a dead or rate-limited call FAILS OPEN to what shipped: ranked retrieval,
  with the deterministic `recency_intent.detect_recency` as the floor for time,
  the same posture as the routing keyword rules under `classify_question`. The
  floor is never consulted when the model answered.

One call replaces the breadth classifier `_whole_scope` used to make, so a
scoped question costs no more than before. Company-wide Ask pays one small
call it did not pay before, because recency matters there too.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from ..vectorstore.base import DateRange
from .recency_intent import RecencyIntent, detect_recency

logger = logging.getLogger(__name__)

SPECIFIC = "specific"
OVERVIEW = "overview"
TIME_NONE = "none"
TIME_RECENT = "recent"
TIME_RANGE = "range"

_BREADTHS = frozenset({SPECIFIC, OVERVIEW})
_TIMES = frozenset({TIME_NONE, TIME_RECENT, TIME_RANGE})

MAX_TOKENS = 80

_PROMPT = """Classify what a search over a company's documents must return to answer a question.
Today is {today} ({weekday}).

Reply with ONE line of JSON and nothing else:
{{"scope": "...", "time": "...", "start": "YYYY-MM-DD or null", "end": "YYYY-MM-DD or null"}}

scope:
  "specific" - the answer lives in a few particular documents: facts, decisions,
               definitions, what one person said, what happened to one thing.
               Includes "summarise X" when X is a single topic, document or person.
  "overview" - the answer requires reading the whole collection: summaries of
               everything, all the goals or themes, "catch me up", "what have we
               been discussing", the direction of the collection as a whole.

time:
  "none"   - time does not narrow the answer. Includes questions about EVERYTHING
             or "from the start", and questions about the current version of a
             policy or fact ("the latest leave policy" is just the leave policy).
  "recent" - asks what happened most recently or what is new, without naming a
             period ("what's the latest on deploys?", "anything new here?").
  "range"  - names or implies a period ("this week", "in March", "since the
             offsite on Monday", "last quarter"). Give start and end as calendar
             dates; end may be null for "until now".

Question: {question}

JSON:"""


@dataclass(frozen=True)
class QueryIntent:
    """The validated plan for one question.

    ``window`` is set only for ``time == "range"``. ``source`` is ``"model"``
    when the classifier answered and ``"fallback"`` when it did not, so a log
    line can tell a model decision from the outage floor.
    """

    breadth: str = SPECIFIC
    time: str = TIME_NONE
    window: DateRange | None = None
    source: str = "fallback"

    @property
    def overview(self) -> bool:
        return self.breadth == OVERVIEW

    @property
    def recency(self) -> RecencyIntent | None:
        """What the retriever acts on: a hard window, a boost, or nothing."""
        if self.time == TIME_RANGE and self.window is not None:
            return RecencyIntent(self.window, f"{self.source}:range")
        if self.time == TIME_RECENT:
            return RecencyIntent(None, f"{self.source}:recent")
        return None


def classify_query_intent(
    question: str,
    *,
    llm=None,
    now: datetime | None = None,
    use_model: bool = True,
) -> QueryIntent:
    """Return the retrieval intent for ``question``. Never raises.

    ``use_model=False`` skips the call (no budget left, or nothing downstream
    would use it) and returns the deterministic floor directly.
    """
    now = now or datetime.now(timezone.utc)
    text = (question or "").strip()
    if not text:
        return QueryIntent()
    if not use_model:
        return _fallback(text, now)

    if llm is None:
        from ..llm.factory import build_llm_provider

        llm = build_llm_provider()

    try:
        reply = llm.generate(
            _PROMPT.format(
                today=now.date().isoformat(),
                weekday=now.strftime("%A"),
                question=text,
            ),
            max_tokens=MAX_TOKENS,
        )
    except Exception:  # noqa: BLE001 - intent is an improvement, never a cost
        logger.warning("query intent classification failed; using the floor", exc_info=True)
        return _fallback(text, now)

    parsed = _parse(reply)
    if parsed is None:
        logger.info("query intent: unparseable reply %r; using the floor", (reply or "")[:80])
        return _fallback(text, now)
    return _validate(parsed, now)


def _fallback(text: str, now: datetime) -> QueryIntent:
    """The outage floor: ranked breadth, and the word-list reading of time."""
    floor = detect_recency(text, now=now)
    if floor is None:
        return QueryIntent()
    if floor.window is not None:
        return QueryIntent(time=TIME_RANGE, window=floor.window)
    return QueryIntent(time=TIME_RECENT)


_JSON_OBJECT = re.compile(r"\{.*?\}", re.S)


def _parse(reply: str | None) -> dict | None:
    """The first JSON object in the reply, tolerating fences and preambles."""
    if not reply:
        return None
    match = _JSON_OBJECT.search(reply)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _validate(raw: dict, now: datetime) -> QueryIntent:
    """Turn the model's reply into something retrieval may act on.

    Validation is the gate, not the prompt. Each field falls back to the value
    with no cost on its own: a bad date loses the window, not the breadth.
    """
    breadth = str(raw.get("scope") or "").strip().lower()
    if breadth not in _BREADTHS:
        breadth = SPECIFIC

    when = str(raw.get("time") or "").strip().lower()
    if when not in _TIMES:
        when = TIME_NONE

    window = None
    if when == TIME_RANGE:
        window = _window(raw.get("start"), raw.get("end"), now)
        if window is None:
            # The model said "a period" but gave no usable one. Boosting recent
            # documents is the reading that cannot exclude the answer.
            when = TIME_RECENT
    return QueryIntent(breadth=breadth, time=when, window=window, source="model")


def _window(start, end, now: datetime) -> DateRange | None:
    """A lenient, sane window from two model-supplied dates, or ``None``.

    One day of slack on each side, because the asker's timezone is unknown and
    a window too narrow is the error that loses the answer. A start in the
    future, or an end before the start, is a misreading, not a window.
    """
    start_d = _date(start)
    end_d = _date(end)
    if start_d is None:
        return None
    today = now.date()
    if start_d > today:
        return None
    if end_d is not None and end_d < start_d:
        return None
    after = datetime.combine(start_d - timedelta(days=1), time.min, tzinfo=timezone.utc)
    before = None
    if end_d is not None and end_d < today:
        before = datetime.combine(end_d + timedelta(days=2), time.min, tzinfo=timezone.utc)
    return DateRange(after=after, before=before)


def _date(value) -> date | None:
    if not isinstance(value, str) or not value.strip() or value.strip().lower() == "null":
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
