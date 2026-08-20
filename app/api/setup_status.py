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
    # ONE round trip, not five.
    #
    # These were five separate `conn.execute` calls. That is five sequential
    # network round trips to Postgres for a signal every single page load needs
    # — and it is paid on top of however far the database actually is. On the
    # live deployment the API runs in US-West while Supabase is in ap-south-1
    # (Mumbai), so each round trip costs ~250ms across the Pacific: ~1.2s of
    # pure latency per /me, before any real work. Folding them into scalar
    # subqueries on one statement makes it one round trip. The predicates are
    # byte-for-byte the same (including `IS NOT DISTINCT FROM`, which is what
    # makes NULL match the org-wide row set), so the result is identical — this
    # is purely fewer trips, not different logic.
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              EXISTS (
                SELECT 1 FROM oauth_connections
                WHERE org_id = %(org)s
                  AND workspace_id IS NOT DISTINCT FROM %(ws)s
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
        # Whether the LEGACY combined "Docs" tab has anything of its own to
        # answer from — deliberately narrower than `ready_to_ask`. A scope can
        # have `ready_to_ask=True` purely because e.g. Slack synced
        # successfully, but Slack already has its own dedicated, source-pinned
        # tab (`app/agent/factory.py::build_slack_agent`); the legacy
        # PolicyAgent/WorkspaceAgent this tab uses is UNSCOPED (no
        # `source_provider` filter), so showing "Docs" here would let it
        # answer straight from those same Slack/Linear/Notion/Drive chunks —
        # exactly the cross-source blending the per-source split exists to
        # prevent. `has_legacy_documents` only counts rows from a provider
        # with no dedicated tab of its own, so "Docs" appears only when there
        # is genuinely something for it, and only it, to answer from.
        "policy_ready": succeeded and legacy_docs,
    }
