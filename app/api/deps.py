"""Shared FastAPI dependencies: session auth + role checks (Phase 13).

Every dependency here reads the session cookie and trusts ONLY what's baked
into its signed claims — never a header, body field, or URL param — so a
request can only ever act on the org_id in its own session. This is the one
place ``org_id`` enters the request lifecycle from the HTTP layer; every
router downstream must take it from here, never from client input.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, HTTPException, Request

from ..agent import build_policy_agent
from ..agent.policy_agent import PolicyAgent
from ..auth.session import SessionClaims, decode_session_token
from ..core.exceptions import AuthError

SESSION_COOKIE_NAME = "session"


@lru_cache(maxsize=1)
def get_policy_agent() -> PolicyAgent:
    """Process-wide singleton agent (loads embedding/reranker models once).

    Building a ``PolicyAgent`` from config loads the local embedding model and
    cross-encoder reranker — expensive per-call, so it happens once per
    process and is reused across requests, the same way ``scripts/cli.py``
    builds it once per session rather than once per turn.
    """
    return build_policy_agent()


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
