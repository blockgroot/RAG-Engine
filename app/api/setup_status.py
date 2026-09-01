"""Shared content readiness signal for org-wide and workspace scopes."""

from __future__ import annotations

from ..db.connection import get_connection


def content_setup_status(org_id: str, workspace_id: str | None = None) -> dict:
    """Compute connection, docs, and sync readiness for one scope."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              EXISTS (
                SELECT 1 FROM oauth_connections
                WHERE org_id = %(org)s
                  AND workspace_id IS NOT DISTINCT FROM %(ws)s
                  -- Saving an LLM key is not connecting a source; without this
                  -- the onboarding wizard marks "Connect" done for an org with
                  -- no documents at all.
                  AND provider <> 'llm'
              ) AS has_connection,
              EXISTS (
                SELECT 1 FROM documents
                WHERE org_id = %(org)s
                  AND workspace_id IS NOT DISTINCT FROM %(ws)s
              ) AS has_documents,
              EXISTS (
                SELECT 1 FROM documents
                WHERE org_id = %(org)s
                  AND workspace_id IS NOT DISTINCT FROM %(ws)s
                  AND source_provider IS DISTINCT FROM 'slack'
                  AND source_provider IS DISTINCT FROM 'linear'
                  AND source_provider IS DISTINCT FROM 'notion'
                  AND source_provider IS DISTINCT FROM 'google'
              ) AS has_legacy_documents,
              EXISTS (
                SELECT 1 FROM ingestion_jobs
                WHERE org_id = %(org)s
                  AND workspace_id IS NOT DISTINCT FROM %(ws)s
                  AND status IN ('queued', 'running')
              ) AS sync_in_progress,
              EXISTS (
                SELECT 1 FROM ingestion_jobs
                WHERE org_id = %(org)s
                  AND workspace_id IS NOT DISTINCT FROM %(ws)s
                  AND status = 'succeeded'
              ) AS has_succeeded_sync,
              (
                SELECT status FROM ingestion_jobs
                WHERE org_id = %(org)s
                  AND workspace_id IS NOT DISTINCT FROM %(ws)s
                ORDER BY created_at DESC LIMIT 1
              ) AS latest_status,
              (
                SELECT doc_count FROM ingestion_jobs
                WHERE org_id = %(org)s
                  AND workspace_id IS NOT DISTINCT FROM %(ws)s
                ORDER BY created_at DESC LIMIT 1
              ) AS latest_doc_count
            """,
            {"org": org_id, "ws": workspace_id},
        ).fetchone()

    (
        has_connection,
        has_documents,
        has_legacy_documents,
        sync_in_progress,
        has_succeeded_sync,
    ) = row[:5]
    latest_job = (row[5], row[6]) if row[5] is not None else None

    docs = bool(has_documents)
    legacy_docs = bool(has_legacy_documents)
    syncing = bool(sync_in_progress)
    succeeded = bool(has_succeeded_sync)
    return {
        "has_connection": bool(has_connection),
        "has_documents": docs,
        "sync_in_progress": syncing,
        "latest_job_status": latest_job[0] if latest_job else None,
        "latest_doc_count": latest_job[1] if latest_job else None,
        "ready_to_ask": succeeded and docs,
        "policy_ready": succeeded and legacy_docs,
    }
