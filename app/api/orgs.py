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
        has_connection = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM oauth_connections WHERE org_id = %s)",
            (session.org_id,),
        ).fetchone()[0]
        has_documents = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM documents WHERE org_id = %s)",
            (session.org_id,),
        ).fetchone()[0]
        # Mid-ingest the first pages land in ``documents`` before the job
        # finishes — that must NOT unlock Ask (false "sync complete").
        sync_in_progress = conn.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM ingestion_jobs "
            "  WHERE org_id = %s AND status IN ('queued', 'running')"
            ")",
            (session.org_id,),
        ).fetchone()[0]
    org_name = row[0] if row else None
    docs = bool(has_documents)
    syncing = bool(sync_in_progress)
    return {
        "user_id": session.user_id,
        "org_id": session.org_id,
        "org_name": org_name,
        "role": session.role,
        "has_connection": bool(has_connection),
        "has_documents": docs,
        "sync_in_progress": syncing,
        # Ready only when the ingest job has finished AND docs exist.
        "ready_to_ask": docs and not syncing,
    }
