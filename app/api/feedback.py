"""Rate an answer, and read what people could not get answered.

Company-wide topics are an admin's; a space's are its OWNER's (a non-member
never reads a space's contents).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from ..core.exceptions import AuthError
from ..feedback import (
    RATING_REASONS,
    Topic,
    assign_topics_soon,
    list_topics,
    mark_handled,
    record_rating,
)
from ..workspaces import assert_member
from ..auth.session import SessionClaims
from .deps import get_session, require_admin, require_workspace_owner

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])

#: Longest comment stored: member text read by an admin, bounded at the boundary.
MAX_COMMENT_CHARS = 1000


@router.post("/chat/feedback")
def submit_feedback(
    body: dict,
    session: SessionClaims = Depends(get_session),
):
    """A thumb on one answer, with an optional reason and comment.

    The conversation must be this person's: a rating carries the question and
    answer text into a table an admin reads.
    """
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


def _topic(t: Topic) -> dict:
    return {
        "id": t.id, "label": t.label, "alerting": t.alerting,
        "missing_people": t.missing_people, "wrong_people": t.wrong_people,
        "questions": t.questions, "best_score": t.best_score,
        "wrong_answer": t.wrong_answer, "wrong_reason": t.wrong_reason,
        "wrong_comment": t.wrong_comment, "surfaces": t.surfaces,
        "last_asked": t.last_asked,
    }


def _topics(org_id: str, workspace_id: str | None) -> dict:
    """Only topics past the alert threshold: the page is where the bell's
    notification leads, so it shows what notified, never one person's miss."""
    assign_topics_soon(org_id)
    return {"topics": [_topic(t) for t in list_topics(org_id, workspace_id) if t.alerting]}


def _handled(org_id: str, workspace_id: str | None, topic_id: str) -> dict:
    try:
        uuid.UUID(topic_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="No such topic") from exc
    if not mark_handled(org_id, workspace_id, topic_id):
        raise HTTPException(status_code=404, detail="No such topic")
    return {"id": topic_id, "handled": True}


@router.get("/admin/feedback")
def admin_feedback(session: SessionClaims = Depends(require_admin)):
    """Company-wide topics people could not get answered, alerting first."""
    return _topics(session.org_id, None)


@router.post("/admin/feedback/topics/{topic_id}/handled")
def admin_topic_handled(topic_id: str, session: SessionClaims = Depends(require_admin)):
    return _handled(session.org_id, None, topic_id)


@router.get("/workspaces/{workspace_id}/feedback")
def space_feedback(workspace_id: str, session: SessionClaims = Depends(get_session),
                   _owner: str = Depends(require_workspace_owner)):
    """This space's topics, for its owner only."""
    return _topics(session.org_id, workspace_id)


@router.post("/workspaces/{workspace_id}/feedback/topics/{topic_id}/handled")
def space_topic_handled(workspace_id: str, topic_id: str,
                        session: SessionClaims = Depends(get_session),
                        _owner: str = Depends(require_workspace_owner)):
    return _handled(session.org_id, workspace_id, topic_id)
