"""Read/write answer feedback and the documentation gaps it reveals.

Two writers, one table (see the `feedback_and_gaps` block in
`app/db/schema.sql`): the answer path records a gap automatically whenever an
answer came back ungrounded, and a member's thumb records a rating. A downvote
on a refusal updates that same row rather than adding a second, because it is
one event -- the system failed to answer, and somebody said so.

Nothing here raises at the caller. A gap log that can fail a question would
trade the product's whole purpose for a diagnostic, so `record_gap` swallows
its own errors. What an admin or space owner reads is grouped by meaning in
``topics.py``.
"""

from __future__ import annotations

import logging
import re

from ..db.connection import get_connection

logger = logging.getLogger(__name__)

#: The fixed downvote categories. A closed set rather than free text because
#: the whole value of a category is that it GROUPS -- "out of date" sends an
#: admin to re-sync a source, "incorrect" sends them to rewrite a document, and
#: two different fixes must not land in one bucket. The comment box is where
#: anything else goes.
RATING_REASONS = ("incorrect", "out_of_date", "unhelpful")

_PUNCT = re.compile(r"[^\w\s]+")
_SPACE = re.compile(r"\s+")


def normalize_question(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    What a thumb matches its question's row by. Grouping questions for the
    admin is by meaning instead (``topics.py``).
    """
    return _SPACE.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def record_gap(
    *,
    org_id: str,
    question: str,
    resolved_question: str | None = None,
    answer: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
    agent: str | None = None,
    surface: str = "web",
    gate_score: float | None = None,
) -> None:
    """Log one unanswered question. Never raises.

    Called from the API edge rather than from the gate inside `RagPipeline`,
    and that placement is the point: a gate miss is only ONE of the ways a
    question goes unanswered. The strict prompt refusing on a gate-passing
    retrieval, the groundedness audit downgrading an answer, and GitHubAgent /
    InsightsAgent refusing with no gate involved at all are the others. The
    edge sees the union of those as `grounded=False`, and it is also the only
    place `user_id` exists.
    """
    question = (question or "").strip()
    if not question:
        return
    normalized = normalize_question(resolved_question or question)
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO feedback_and_gaps "
                "(org_id, workspace_id, user_id, conversation_id, question, "
                " normalized_question, answer, agent, surface, refusal, gate_score) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s)",
                (
                    org_id,
                    workspace_id,
                    user_id,
                    conversation_id,
                    question,
                    normalized,
                    answer,
                    agent,
                    surface,
                    gate_score,
                ),
            )
    except Exception:  # noqa: BLE001 - a diagnostic must never fail an answer
        logger.warning("Feedback: could not record gap", exc_info=True)


def record_rating(
    *,
    org_id: str,
    user_id: str,
    conversation_id: str,
    question: str,
    rating: int,
    resolved_question: str | None = None,
    answer: str | None = None,
    workspace_id: str | None = None,
    agent: str | None = None,
    reason: str | None = None,
    comment: str | None = None,
) -> str:
    """Rate one answer. Returns the row id.

    Updates the most recent row for the same (conversation, normalized
    question) when there is one -- which is what makes a thumbs-down on a
    refusal a single event rather than a gap row plus a rating row -- and
    inserts otherwise. Re-clicking a thumb therefore corrects the rating
    instead of stuffing the ballot.
    """
    if rating not in (-1, 1):
        raise ValueError("rating must be -1 or +1")
    if reason is not None and reason not in RATING_REASONS:
        raise ValueError(f"unknown reason: {reason}")

    normalized = normalize_question(resolved_question or question)
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE feedback_and_gaps SET rating = %s, reason = %s, comment = %s "
            "WHERE id = ("
            "  SELECT id FROM feedback_and_gaps"
            "  WHERE org_id = %s AND conversation_id = %s"
            "    AND normalized_question = %s"
            "  ORDER BY created_at DESC LIMIT 1"
            ") RETURNING id::text",
            (
                rating,
                reason,
                comment,
                org_id,
                conversation_id,
                normalized,
            ),
        ).fetchone()
        if row is not None:
            return row[0]

        row = conn.execute(
            "INSERT INTO feedback_and_gaps "
            "(org_id, workspace_id, user_id, conversation_id, question, "
            " normalized_question, answer, agent, rating, reason, comment) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id::text",
            (
                org_id,
                workspace_id,
                user_id,
                conversation_id,
                question.strip(),
                normalized,
                answer,
                agent,
                rating,
                reason,
                comment,
            ),
        ).fetchone()
    return row[0]


#: The agent key of Chart mode (`agent.orchestration.INSIGHTS_KEY`). A chart
#: it could not draw is not a document nobody wrote: it is recorded for us
#: and never grouped into a topic an admin sees.
CHART_AGENT = "insights"
