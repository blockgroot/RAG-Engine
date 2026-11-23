"""Ask a friend routes: who can receive, whether the asker must tick, send.

Thin: identity comes from the session (``deps.get_session``); the rules live in
``app.askfriend.service``.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from ..askfriend import service
from ..auth.session import SessionClaims
from .deps import get_session

router = APIRouter(tags=["ask-friend"])


@router.get("/ask-friend/people")
def ask_friend_people(workspace_id: str | None = None,
                      session: SessionClaims = Depends(get_session)) -> dict:
    return {"people": service.people(session, workspace_id)}


@router.post("/ask-friend/check")
def ask_friend_check(body: dict = Body(...), session: SessionClaims = Depends(get_session)) -> dict:
    return service.check(session, body)


@router.post("/ask-friend/send")
def ask_friend_send(body: dict = Body(...), session: SessionClaims = Depends(get_session)) -> dict:
    return service.send(session, body)
