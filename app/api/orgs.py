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
    # Org name and the GitHub flag in ONE round trip. They were two separate
    # `with get_connection()` blocks, which — together with the five queries
    # content_setup_status used to issue — made this endpoint SEVEN sequential
    # round trips to Postgres. /me runs on every page load, and on the live
    # deployment the API is in US-West while the database is in ap-south-1
    # (Mumbai), so each trip costs ~250ms across the Pacific: ~1.75s of pure
    # network before the page could render anything. Now two.
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT name FROM organizations WHERE id = %(org)s) AS org_name,
              (SELECT email FROM users WHERE id = %(user)s) AS email,
              EXISTS (
                SELECT 1 FROM oauth_connections
                WHERE org_id = %(org)s
                  AND provider = 'github'
                  AND workspace_id IS NULL
              ) AS github_connected,
              EXISTS (
                SELECT 1 FROM documents
                WHERE org_id = %(org)s
                  AND source_provider = 'slack'
                  AND workspace_id IS NULL
              ) AS slack_ready
            """,
            {"org": session.org_id, "user": session.user_id},
        ).fetchone()

    status = content_setup_status(session.org_id, workspace_id=None)
    org_name = row[0] if row else None
    email = row[1] if row else None
    github_connected = bool(row[2]) if row else False
    slack_ready = bool(row[3]) if row else False

    return {
        "user_id": session.user_id,
        "org_id": session.org_id,
        "org_name": org_name,
        "email": email,
        "role": session.role,
        # Whether the chat UI should offer its "Code" tab. Reported here rather
        # than read from /admin/connections because that route is admin-only,
        # and ordinary members must be able to ask GitHub questions too — they
        # just can't manage the connection. Only a boolean is exposed: no
        # repository names, since this is the one endpoint every session can
        # call and connection detail belongs behind require_admin.
        # Resolved in the combined query above. `workspace_id IS NULL` is
        # explicit there: a personal sub-workspace connection must not light up
        # the org-wide chat's Code tab.
        "github_connected": github_connected,
        # Whether the chat UI should offer its "Slack" tab. Deliberately keyed on
        # ingested Slack *documents*, not on the connection existing: unlike
        # GitHub (live reads, answerable the moment it is linked) Slack is a
        # retrieval source, so a connection whose first sync hasn't produced
        # any threads yet would give an empty tab that can only ever refuse.
        "slack_ready": slack_ready,
        **status,
    }
