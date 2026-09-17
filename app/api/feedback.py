"""Rate an answer, and read what the company could not answer.

Two routes with two different audiences, deliberately in one module because
they are two ends of one pipe: a member marks an answer wrong, an admin reads
the list of what to write down. Splitting them would put the writer and the
reader of the same table in two files that have to agree.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..core.exceptions import AuthError
from ..feedback import (
    MAX_ROWS,
    RATING_REASONS,
    list_downvoted,
    list_gaps,
    rating_counts,
    record_rating,
)
from ..security.rate_limit import check_rate_limit
from ..workspaces import assert_member
from ..auth.session import SessionClaims
from .deps import get_session, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])

#: Longest comment we store. A downvote comment is a sentence about what went
#: wrong, not a document -- and it is member-supplied text going into a table
#: an admin reads, so it is bounded at the boundary like every other upload.
MAX_COMMENT_CHARS = 1000

#: The gap view's window, in days. Clamped rather than free: `days` is bound as
#: a parameter, but an unbounded window on a busy tenant is a slow query
#: nobody asked for.
MAX_WINDOW_DAYS = 365


@router.post("/chat/feedback")
def submit_feedback(
    body: dict,
    session: SessionClaims = Depends(get_session),
):
    """A thumb on one answer, with an optional reason and comment.

    The conversation must be this person's, checked with the same function the
    chat and attachment routes use -- a rating carries the question and answer
    text, so accepting an id without the owner check would let anyone write
    another member's exchange into a table their admin reads.
    """
    check_rate_limit(f"feedback:{session.org_id}:{session.user_id}")

    conversation_id = (body.get("conversation_id") or "").strip()
    question = (body.get("question") or "").strip()
    if not conversation_id or not question:
        raise HTTPException(
            status_code=400, detail="A conversation and a question are required"
        )

    rating = body.get("rating")
    if rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="rating must be -1 or 1")

    reason = body.get("reason")
    if reason is not None and reason not in RATING_REASONS:
        raise HTTPException(status_code=400, detail="Unknown reason")

    comment = (body.get("comment") or "").strip() or None
    if comment and len(comment) > MAX_COMMENT_CHARS:
        comment = comment[:MAX_COMMENT_CHARS]

    workspace_id = body.get("workspace_id")
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    from .chat import _conversation_belongs_to_scope

    if not _conversation_belongs_to_scope(
        conversation_id, session.org_id, workspace_id, session.user_id
    ):
        raise HTTPException(
            status_code=404, detail="No such conversation for this organization"
        )

    row_id = record_rating(
        org_id=session.org_id,
        user_id=session.user_id,
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        question=question,
        resolved_question=body.get("resolved_question"),
        answer=body.get("answer"),
        agent=body.get("agent"),
        rating=int(rating),
        reason=reason,
        comment=comment,
    )
    return {"id": row_id, "rating": int(rating)}


@router.get("/admin/feedback")
def admin_feedback(
    days: int = 30,
    session: SessionClaims = Depends(require_admin),
):
    """What this company asked and could not get answered.

    ONE round trip for all three lists, the `/insights/dashboard` posture: a
    page that arrives in pieces on a cold free instance reads as broken rather
    than slow.
    """
    days = max(1, min(int(days), MAX_WINDOW_DAYS))
    gaps = list_gaps(org_id=session.org_id, days=days, limit=MAX_ROWS)
    downvoted = list_downvoted(org_id=session.org_id, days=days, limit=MAX_ROWS)
    return {
        "days": days,
        "counts": rating_counts(org_id=session.org_id, days=days),
        "gaps": [
            {
                "question": g.question,
                "asked_by": g.asked_by,
                "refusals": g.refusals,
                "downvotes": g.downvotes,
                # NULL means retrieval matched nothing at all; a number means
                # something was close but under the gate. Different fixes:
                # write the document, versus find out why the one we have is
                # unreachable.
                "best_gate_score": g.best_gate_score,
                "last_asked": g.last_asked,
            }
            for g in gaps
        ],
        "downvoted": [
            {
                "id": r.id,
                "question": r.question,
                "answer": r.answer,
                "agent": r.agent,
                "reason": r.reason,
                "comment": r.comment,
                "created_at": r.created_at,
            }
            for r in downvoted
        ],
    }
