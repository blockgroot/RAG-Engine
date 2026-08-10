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

from ..agent import build_github_agent, build_policy_agent, build_workspace_agent
from ..agent.github_agent import GitHubAgent
from ..agent.policy_agent import PolicyAgent
from ..agent.workspace_agent import WorkspaceAgent
from ..auth.session import SessionClaims, decode_session_token
from ..core.exceptions import AuthError
from ..db.connection import get_connection
from ..vectorstore import build_vector_store
from ..vectorstore.base import VectorStore

SESSION_COOKIE_NAME = "session"


@lru_cache(maxsize=1)
def get_policy_agent() -> PolicyAgent:
    """Process-wide singleton agent (loads embedding/reranker models once).

    Building a ``PolicyAgent`` from config loads the local embedding model and
    cross-encoder reranker — expensive per-call, so it happens once per
    process and is reused across requests. Local embedder/reranker factories
    are also process-wide singletons, so a later ``get_workspace_agent()``
    shares the same weights instead of loading a second copy into RAM.

    Chat routes call this lazily (only when policy answers are needed) — do
    not ``Depends()`` it alongside ``get_workspace_agent`` on every request.
    """
    return build_policy_agent()


@lru_cache(maxsize=1)
def get_workspace_agent() -> WorkspaceAgent:
    """Process-wide singleton ``WorkspaceAgent`` (Workspace-within-a-Workspace).

    A second, independent pipeline from ``get_policy_agent`` (own prompt
    framing + fallback string, web-search off) — see
    ``app/agent/workspace_agent.py``. Still built once per process like the
    policy agent, for the same reason (embedding/reranker model load cost).
    """
    return build_workspace_agent()


@lru_cache(maxsize=1)
def get_github_agent() -> GitHubAgent:
    """Process-wide singleton ``GitHubAgent``.

    Cheap to build compared to the other two — no embedding model and no
    reranker, because this agent has no retrieval at all. Cached anyway for
    consistency, and safe to cache because it holds **no** tenant state: the
    GitHub reader (and therefore the installation token and authorized repo
    scope) is constructed per request from the caller's ``org_id``. See
    ``app/agent/github_agent.py`` on why ``reader_builder`` is a builder.
    """
    return build_github_agent()


@lru_cache(maxsize=1)
def get_vector_store() -> VectorStore:
    """Process-wide singleton vector store (just a pooled DB connection)."""
    return build_vector_store()


def get_session(request: Request) -> SessionClaims:
    """Decode the session cookie and confirm the user+org still exist.

    A JWT alone is not enough after a DB wipe / org delete: the signed claims
    can outlive the rows they point at. Without this check, Connect Notion and
    other FK-backed writes 500 with foreign-key errors instead of a clean 401.

    ``role`` is re-read from ``users`` on every request (not taken from the JWT).
    Sessions live up to 30 days; promote/demote must take effect immediately
    without waiting for re-login. The JWT still carries ``role`` for
    observability / older clients; authorization uses the live value.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        claims = decode_session_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    with get_connection() as conn:
        row = conn.execute(
            "SELECT u.sessions_revoked_at, u.role FROM users u "
            "JOIN organizations o ON o.id = u.org_id "
            "WHERE u.id = %s AND u.org_id = %s",
            (claims.user_id, claims.org_id),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=401,
            detail="Session is no longer valid — please sign in again",
        )
    revoked_at, live_role = row
    if revoked_at is not None and claims.issued_at <= revoked_at:
        raise HTTPException(status_code=401, detail="Session has been revoked")
    return SessionClaims(
        user_id=claims.user_id,
        org_id=claims.org_id,
        role=live_role,
        issued_at=claims.issued_at,
    )


def require_admin(session: SessionClaims = Depends(get_session)) -> SessionClaims:
    if session.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return session


def get_workspace_role(
    workspace_id: str,
    session: SessionClaims = Depends(get_session),
) -> str:
    """Resolve + validate workspace membership; return the caller's role.

    Mirrors ``require_admin``'s shape but for the workspace boundary
    (Workspace-within-a-Workspace) — the ONE place a ``workspace_id`` from a
    URL path is checked against the session's ``org_id`` + ``user_id`` before
    any router uses it. ``assert_member`` also checks the workspace's own
    ``org_id`` against the caller's, so a stale/forged ``workspace_id`` from a
    different org fails closed here rather than resolving to real data.
    """
    from ..core.exceptions import AuthError
    from ..workspaces import assert_member

    try:
        return assert_member(workspace_id, session.org_id, session.user_id)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def require_workspace_owner(role: str = Depends(get_workspace_role)) -> str:
    if role != "owner":
        raise HTTPException(status_code=403, detail="Workspace owner role required")
    return role
