"""Shared "is this corpus ready to Ask?" signal for org-wide and workspaces.

Both ``GET /me`` and ``GET /workspaces/{id}`` expose the same shape so the
frontend can gate Ask consistently. Mid-ingest document rows must NOT unlock
Ask — that would invite answers from a partial corpus.
"""

from __future__ import annotations

from ..db.connection import get_connection


def content_setup_status(org_id: str, workspace_id: str | None = None) -> dict:
    """Compute connection/docs/sync readiness for one scoped corpus.

    ``workspace_id=None`` is the org-wide row set (``workspace_id IS NULL``);
    a concrete id is that sub-workspace only — never blended with org-wide or
    sibling workspaces.
    """
    with get_connection() as conn:
        has_connection = conn.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM oauth_connections "
            "  WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s"
            ")",
            (org_id, workspace_id),
        ).fetchone()[0]
        has_documents = conn.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM documents "
            "  WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s"
            ")",
            (org_id, workspace_id),
        ).fetchone()[0]
        sync_in_progress = conn.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM ingestion_jobs "
            "  WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "    AND status IN ('queued', 'running')"
            ")",
            (org_id, workspace_id),
        ).fetchone()[0]
        has_succeeded_sync = conn.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM ingestion_jobs "
            "  WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "    AND status = 'succeeded'"
            ")",
            (org_id, workspace_id),
        ).fetchone()[0]
        latest_job = conn.execute(
            "SELECT status, doc_count FROM ingestion_jobs "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "ORDER BY created_at DESC LIMIT 1",
            (org_id, workspace_id),
        ).fetchone()

    docs = bool(has_documents)
    syncing = bool(sync_in_progress)
    succeeded = bool(has_succeeded_sync)
    return {
        "has_connection": bool(has_connection),
        "has_documents": docs,
        "sync_in_progress": syncing,
        "latest_job_status": latest_job[0] if latest_job else None,
        "latest_doc_count": latest_job[1] if latest_job else None,
        "ready_to_ask": succeeded and docs and not syncing,
    }
