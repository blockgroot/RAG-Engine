"""``/me``: the signed-in user's identity, for the frontend to bootstrap."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config.settings import UpdateSettings
from ..db.connection import get_connection
from .deps import SessionClaims, get_session
from .setup_status import content_setup_status

router = APIRouter(tags=["me"])



def _act_on(org_id: str) -> bool:
    from ..actions.policy import act_on

    return act_on(org_id)

@router.get("/me")
def me(session: SessionClaims = Depends(get_session)):
    """Session bootstrap and org-wide setup readiness.

    ``ready_to_ask`` needs a succeeded org-wide ingest with nothing org-wide
    queued; space syncs have their own gate (``GET /workspaces/{id}``).
    """
    # Org name and the GitHub flag in one round trip: /me runs on every page
    # load and each trip to the database can cost ~250ms.
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
              ) AS slack_ready,
              EXISTS (
                SELECT 1 FROM documents
                WHERE org_id = %(org)s
                  AND source_provider = 'linear'
                  AND workspace_id IS NULL
              ) AS linear_ready,
              EXISTS (
                SELECT 1 FROM documents
                WHERE org_id = %(org)s
                  AND source_provider = 'notion'
                  AND workspace_id IS NULL
              ) AS notion_ready,
              EXISTS (
                SELECT 1 FROM documents
                WHERE org_id = %(org)s
                  AND source_provider = 'google'
                  AND workspace_id IS NULL
              ) AS drive_ready,
              (SELECT display_name FROM users WHERE id = %(user)s) AS display_name
            """,
            {"org": session.org_id, "user": session.user_id},
        ).fetchone()

    status = content_setup_status(session.org_id, workspace_id=None)
    org_name = row[0] if row else None
    email = row[1] if row else None
    github_connected = bool(row[2]) if row else False
    slack_ready = bool(row[3]) if row else False
    linear_ready = bool(row[4]) if row else False
    notion_ready = bool(row[5]) if row else False
    drive_ready = bool(row[6]) if row else False
    display_name = row[7] if row else None

    return {
        "user_id": session.user_id,
        "org_id": session.org_id,
        "org_name": org_name,
        "email": email,
        # What the person asked to be called; None until they have said.
        "name": display_name,
        "role": session.role,
        # Whether chat offers the "Code" tab. Here because every member may ask
        # GitHub but /admin/connections is admin-only; a boolean only, no repo names.
        # Org-wide only (`workspace_id IS NULL` in the query above).
        "github_connected": github_connected,
        # Whether to show Act (writes into connected tools): the company's
        # admins allowed it. The Act page itself says what is switched on.
        "act_enabled": _act_on(session.org_id),
        # Only a self-hosted copy has versions to install; Cloud is kept
        # current for its companies, so it shows no Updates page.
        "selfhosted": UpdateSettings.from_env().deployment == "selfhosted",
        # "Slack" tab: keyed on ingested threads, not the connection (a retrieval
        # source with nothing synced can only refuse).
        "slack_ready": slack_ready,
        # "Linear" tab: same rule, keyed on ingested issues.
        "linear_ready": linear_ready,
        # Notion and Drive get separate tabs so an answer is traceable to one source.
        "notion_ready": notion_ready,
        "drive_ready": drive_ready,
        **status,
    }
