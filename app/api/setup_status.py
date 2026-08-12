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
        # Deliberately NOT `and not syncing`. A *first* sync is still correctly
        # gated, because until it finishes there is no succeeded job and no
        # documents — `succeeded and docs` is False on its own. What the extra
        # `not syncing` term did was lock the user out again every time a
        # LATER sync was queued, even though a full sync had already succeeded
        # and the content was sitting there answerable. Observed in production:
        # onboarding completed 11 pages / 68 chunks, then one redundant queued
        # job pinned the page on "Bringing your policies in…" indefinitely with
        # no way forward. Re-syncing is the normal steady state for an existing
        # org (the Sources page's "Update policies" does it while people are
        # asking questions), and incremental sync only upserts changed pages —
        # it never empties the corpus — so an in-flight sync is no reason to
        # withdraw Ask. Callers that genuinely want "is something running right
        # now" have `sync_in_progress` for exactly that, and should surface it
        # as a passive indicator rather than a gate.
        "ready_to_ask": succeeded and docs,
    }
