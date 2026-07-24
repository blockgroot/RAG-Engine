"""Shared FastAPI dependencies: session auth + role checks (Phase 13).

Every dependency here reads the session cookie and trusts ONLY what's baked
into its signed claims — never a header, body field, or URL param — so a
request can only ever act on the org_id in its own session. This is the one
place ``org_id`` enters the request lifecycle from the HTTP layer; every
router downstream must take it from here, never from client input.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from ..auth.session import SessionClaims, decode_session_token
from ..core.exceptions import AuthError

SESSION_COOKIE_NAME = "session"


def get_session(request: Request) -> SessionClaims:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return decode_session_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require_admin(session: SessionClaims = Depends(get_session)) -> SessionClaims:
    if session.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return session
