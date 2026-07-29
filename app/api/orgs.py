"""``/me`` — lets the frontend bootstrap the signed-in user's identity (Phase 13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..db.connection import get_connection
from .deps import SessionClaims, get_session

router = APIRouter(tags=["me"])


@router.get("/me")
def me(session: SessionClaims = Depends(get_session)):
    """Session bootstrap + setup readiness.

    ``ready_to_ask`` is True only after a full ingest job has *succeeded* for
    this org (and nothing is still queued/running). Mid-ingest document rows
    must NOT unlock Ask — that would invite answers from a partial corpus.
    """
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
        sync_in_progress = conn.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM ingestion_jobs "
            "  WHERE org_id = %s AND status IN ('queued', 'running')"
            ")",
            (session.org_id,),
        ).fetchone()[0]
        # At least one finished sync for this org (full Notion pull completed).
        has_succeeded_sync = conn.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM ingestion_jobs "
            "  WHERE org_id = %s AND status = 'succeeded'"
            ")",
            (session.org_id,),
        ).fetchone()[0]
        latest_job = conn.execute(
            "SELECT status, doc_count FROM ingestion_jobs "
            "WHERE org_id = %s ORDER BY created_at DESC LIMIT 1",
            (session.org_id,),
        ).fetchone()

    org_name = row[0] if row else None
    docs = bool(has_documents)
    syncing = bool(sync_in_progress)
    succeeded = bool(has_succeeded_sync)
    latest_status = latest_job[0] if latest_job else None
    latest_doc_count = latest_job[1] if latest_job else None

    return {
        "user_id": session.user_id,
        "org_id": session.org_id,
        "org_name": org_name,
        "role": session.role,
        "has_connection": bool(has_connection),
        "has_documents": docs,
        "sync_in_progress": syncing,
        "latest_job_status": latest_status,
        "latest_doc_count": latest_doc_count,
        # Full sync finished; safe for the agent to answer from this org's docs.
        "ready_to_ask": succeeded and docs and not syncing,
    }
