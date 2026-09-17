"""Read/write answer feedback and the documentation gaps it reveals.

Two writers, one table (see the `feedback_and_gaps` block in
`app/db/schema.sql`): the answer path records a gap automatically whenever an
answer came back ungrounded, and a member's thumb records a rating. A downvote
on a refusal updates that same row rather than adding a second, because it is
one event -- the system failed to answer, and somebody said so.

Nothing here raises at the caller. A gap log that can fail a question would
trade the product's whole purpose for a diagnostic, so `record_gap` swallows
its own errors; the read paths are only ever called from an admin route, where
a 500 is honest.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..db.connection import get_connection

logger = logging.getLogger(__name__)

#: The fixed downvote categories. A closed set rather than free text because
#: the whole value of a category is that it GROUPS -- "out of date" sends an
#: admin to re-sync a source, "incorrect" sends them to rewrite a document, and
#: two different fixes must not land in one bucket. The comment box is where
#: anything else goes.
RATING_REASONS = ("incorrect", "out_of_date", "unhelpful")

#: How many rows the admin views return. A gap list is read by eye; past a
#: couple of screens nobody scans it, and a bigger LIMIT would only make the
#: page slower at being unread.
MAX_ROWS = 50

_PUNCT = re.compile(r"[^\w\s]+")
_SPACE = re.compile(r"\s+")


def normalize_question(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    This is what the gap view GROUPs by, and it is deliberately crude.

    ponytail: exact-text grouping after normalization. It catches people who
    phrase a question the same way and nothing else, so "how do I claim
    medical expenses" and "health insurance reimbursement" stay two rows. The
    upgrade is to embed each gap question (the embedder and pgvector are
    already here) and group by cosine -- worth doing when the list is too long
    to read, which is the only point at which grouping starts earning its
    keep. Until then a row of count 1 is still a question nobody could answer.
    """
    return _SPACE.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


@dataclass(frozen=True)
class GapRow:
    """One normalized question, aggregated over the window."""

    question: str
    normalized_question: str
    asked_by: int
    refusals: int
    downvotes: int
    #: NULL when retrieval matched nothing at all; a number when something was
    #: close but below the gate.
    best_gate_score: float | None
    last_asked: str


@dataclass(frozen=True)
class RatedRow:
    """One downvoted answer, with whatever the member said about it."""

    id: str
    question: str
    answer: str | None
    agent: str | None
    reason: str | None
    comment: str | None
    created_at: str


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


def list_gaps(*, org_id: str, days: int = 30, limit: int = MAX_ROWS) -> list[GapRow]:
    """Questions this org could not answer, grouped, worst first.

    Ordered by the number of DISTINCT people who hit it, then by how often --
    "nine people asked about parental leave" is the line that gets a document
    written, and one person asking nine times is not the same fact.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT max(question), normalized_question, "
            "       count(DISTINCT user_id), "
            "       count(*) FILTER (WHERE refusal), "
            "       count(*) FILTER (WHERE rating = -1), "
            "       max(gate_score), "
            "       max(created_at)::text "
            "FROM feedback_and_gaps "
            "WHERE org_id = %s AND created_at > now() - make_interval(days => %s) "
            "  AND (refusal OR rating = -1) "
            "GROUP BY normalized_question "
            "ORDER BY count(DISTINCT user_id) DESC, count(*) DESC "
            "LIMIT %s",
            (org_id, days, limit),
        ).fetchall()
    return [GapRow(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows]


def list_downvoted(
    *, org_id: str, days: int = 30, limit: int = MAX_ROWS
) -> list[RatedRow]:
    """Answers a member marked wrong, newest first, with their reason."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id::text, question, answer, agent, reason, comment, "
            "       created_at::text "
            "FROM feedback_and_gaps "
            "WHERE org_id = %s AND rating = -1 "
            "  AND created_at > now() - make_interval(days => %s) "
            "ORDER BY created_at DESC LIMIT %s",
            (org_id, days, limit),
        ).fetchall()
    return [RatedRow(*r) for r in rows]


def rating_counts(*, org_id: str, days: int = 30) -> dict[str, int]:
    """Up / down / refusal totals for the window."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT count(*) FILTER (WHERE rating = 1), "
            "       count(*) FILTER (WHERE rating = -1), "
            "       count(*) FILTER (WHERE refusal) "
            "FROM feedback_and_gaps "
            "WHERE org_id = %s AND created_at > now() - make_interval(days => %s)",
            (org_id, days),
        ).fetchone()
    return {"up": row[0], "down": row[1], "refusals": row[2]}
