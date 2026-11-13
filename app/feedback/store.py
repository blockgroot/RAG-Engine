"""Read/write answer feedback and the documentation gaps it reveals.

One table: an ungrounded answer records a gap, a thumb a rating; a downvote
on a refusal updates that same row (one event). ``record_gap`` never raises:
a diagnostic must not fail a question.
"""

from __future__ import annotations

import logging
import re

from ..db.connection import get_connection

logger = logging.getLogger(__name__)

#: Fixed downvote categories: a closed set groups by fix ("out of date" means
#: re-sync, "incorrect" means rewrite). Anything else goes in the comment.
RATING_REASONS = ("incorrect", "out_of_date", "unhelpful")

_PUNCT = re.compile(r"[^\w\s]+")
_SPACE = re.compile(r"\s+")


def normalize_question(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace: how a thumb finds
    its question's row.
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

    Called from the API edge, not the gate: the edge sees every way a
    question goes unanswered (gate, strict prompt, audit, agents) and knows
    the `user_id`.
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

    Updates the latest row for the same (conversation, normalized question),
    else inserts, so a re-click corrects rather than adds.
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


#: Chart mode's agent key: its refusals are ours, never an admin's topic.
CHART_AGENT = "insights"
