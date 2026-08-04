"""``/me`` — lets the frontend bootstrap the signed-in user's identity (Phase 13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..db.connection import get_connection
from .deps import SessionClaims, get_session
from .setup_status import content_setup_status

router = APIRouter(tags=["me"])


@router.get("/me")
def me(session: SessionClaims = Depends(get_session)):
    """Session bootstrap + org-wide setup readiness.

    ``ready_to_ask`` is True only after a full *org-wide* ingest job has
    *succeeded* (and nothing org-wide is still queued/running). Workspace
    syncs must not unlock (or block) org Ask — they have their own gate on
    ``GET /workspaces/{id}``.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT name FROM organizations WHERE id = %s", (session.org_id,)
        ).fetchone()

    status = content_setup_status(session.org_id, workspace_id=None)
    org_name = row[0] if row else None

    return {
        "user_id": session.user_id,
        "org_id": session.org_id,
        "org_name": org_name,
        "role": session.role,
        **status,
    }
