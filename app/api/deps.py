"""Shared FastAPI dependencies: session auth and role checks.

The one place ``org_id`` enters a request: only from the signed session
cookie, never a header, body or URL.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import Depends, HTTPException, Request

from ..agent import (
    build_drive_agent,
    build_github_agent,
    build_insights_agent,
    build_linear_agent,
    build_notion_agent,
    build_policy_agent,
    build_slack_agent,
    build_workspace_agent,
)
from ..agent.drive_agent import DriveAgent
from ..agent.github_agent import GitHubAgent
from ..agent.insights_agent import InsightsAgent
from ..agent.linear_agent import LinearAgent
from ..agent.notion_agent import NotionAgent
from ..agent.policy_agent import PolicyAgent
from ..agent.slack_agent import SlackAgent
from ..agent.workspace_agent import WorkspaceAgent
from ..auth.session import SessionClaims, decode_session_token
from ..core.exceptions import AuthError
from ..auth.users import get_user
from ..config.settings import ApiSettings
from ..db.connection import get_connection
from ..sources.google_groups import viewer_for_person
from ..vectorstore.base import Viewer

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session"
# First-party cookie: the browser calls the frontend origin's /api. Keep Lax;
# do not switch to None for a split-domain deploy.
SESSION_COOKIE_FLAGS: dict = {
    "httponly": True,
    "secure": True,
    "samesite": "lax",
    "path": "/",
}


def public_api_url(request: Request) -> str:
    """Base for links to this API that land in an inbox.

    Self-hosted, this process sees a container name; the frontend origin plus
    `/api` reaches the API everywhere.
    """
    frontend = ApiSettings.from_env().frontend_url
    if frontend:
        return f"{frontend.rstrip('/')}/api"
    return str(request.base_url).rstrip("/")


@lru_cache(maxsize=1)
def get_policy_agent() -> PolicyAgent:
    """Process-wide ``PolicyAgent`` (the embedder and reranker load once).

    Called lazily by chat routes; do not ``Depends()`` it on every request.
    """
    return build_policy_agent()


@lru_cache(maxsize=1)
def get_workspace_agent() -> WorkspaceAgent:
    """Process-wide ``WorkspaceAgent``, sharing the same model singletons."""
    return build_workspace_agent()


@lru_cache(maxsize=1)
def get_slack_agent() -> SlackAgent:
    """Process-wide ``SlackAgent`` (pinned to ``source_provider="slack"``)."""
    return build_slack_agent()


@lru_cache(maxsize=1)
def get_linear_agent() -> LinearAgent:
    """Process-wide ``LinearAgent`` (pinned to ``source_provider="linear"``)."""
    return build_linear_agent()


@lru_cache(maxsize=1)
def get_notion_agent() -> NotionAgent:
    """Process-wide ``NotionAgent``, pinned to Notion so answers never blend with Drive."""
    return build_notion_agent()


@lru_cache(maxsize=1)
def get_drive_agent() -> DriveAgent:
    """Process-wide singleton ``DriveAgent`` (Google Drive-only retrieval)."""
    return build_drive_agent()


@lru_cache(maxsize=1)
def get_github_agent() -> GitHubAgent:
    """Process-wide ``GitHubAgent`` (no retrieval).

    Holds no tenant state: the GitHub reader is built per request from ``org_id``.
    """
    return build_github_agent()


@lru_cache(maxsize=1)
def get_insights_agent() -> InsightsAgent:
    """Process-wide singleton. Holds no tenant state and no model weights."""
    return build_insights_agent()


def get_session(request: Request) -> SessionClaims:
    """Decode the session cookie and confirm the user and org still exist.

    Signed claims can outlive their rows (a 401, not FK 500s). ``role`` is
    re-read from ``users`` each request so promote/demote apply at once.
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
    """Check the caller is a member of ``workspace_id``; return their role.

    The one place a path ``workspace_id`` is checked against the session;
    another org's workspace fails closed (403).
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


def viewer_for(session) -> Viewer:
    """The ``Viewer`` for a signed-in session: who may read which documents.

    Resolved here from the session, like ``org_id``; the email comes from the
    ``users`` row. No session at all is ``unrestricted()`` (internal, CLI); a
    signed-in user whose row cannot be read is ``public_only``, failing closed.
    """
    if session is None:
        return Viewer.unrestricted()
    try:
        user = get_user(session.user_id)
    except Exception:  # noqa: BLE001 - never fail a question over an identity read
        logger.exception("Could not resolve viewer for user %s", session.user_id)
        return Viewer.public_only_viewer()
    if user is None or not user.email:
        return Viewer.public_only_viewer()
    # Through `viewer_for_person`, never `Viewer(email=...)`: it adds the asker's
    # Google Group memberships, which `group:` grants need.
    return viewer_for_person(session.org_id, user.email)
