"""``/me`` — lets the frontend bootstrap the signed-in user's identity (Phase 13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..db.connection import get_connection
from .deps import SessionClaims, get_session

router = APIRouter(tags=["me"])


@router.get("/me")
def me(session: SessionClaims = Depends(get_session)):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT name FROM organizations WHERE id = %s", (session.org_id,)
        ).fetchone()
        # Whether there's anything to actually chat about yet — the frontend
        # uses this to gate /chat behind connecting a data source first,
        # rather than letting a brand-new org land on an empty chat screen.
        has_documents = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM documents WHERE org_id = %s)", (session.org_id,)
        ).fetchone()[0]
    org_name = row[0] if row else None
    return {
        "user_id": session.user_id,
        "org_id": session.org_id,
        "org_name": org_name,
        "role": session.role,
        "has_documents": has_documents,
    }
