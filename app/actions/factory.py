"""Build the actions this person may be offered right now.

The registry is the whole menu: an action that is not a class here cannot be
proposed, whatever the model asks for. Offered means all three hold -- an admin
allowed Act for the company, the org switched the action on and this space did
not switch it off, and the person connected that tool themselves.
"""

from __future__ import annotations

from ..auth.user_connections import connected_providers
from . import policy
from .base import Action
from .drive import DriveAppendRows, DriveCreateDoc, DriveCreateSheet
from .github import GitHubComment, GitHubCreateIssue, GitHubLabelAssign
from .linear import LinearComment, LinearCreateIssue, LinearUpdateIssue
from .notion import NotionAddRow, NotionAppend, NotionComment, NotionCreatePage
from .slack import SlackPostMessage

#: Every action that exists, by name. Nothing else can ever be proposed.
ACTIONS: dict[str, type[Action]] = {
    cls.name: cls
    for cls in (
        LinearCreateIssue, LinearComment, LinearUpdateIssue,
        GitHubCreateIssue, GitHubComment, GitHubLabelAssign,
        NotionComment, NotionAppend, NotionCreatePage, NotionAddRow,
        DriveCreateDoc, DriveCreateSheet, DriveAppendRows, SlackPostMessage,
    )
}


def build_actions(org_id: str, workspace_id: str | None, user_id: str) -> list[Action]:
    if not policy.act_on(org_id, workspace_id):
        return []
    enabled = policy.enabled_actions(org_id, workspace_id)
    if not enabled:
        return []
    providers = connected_providers(org_id, user_id) | slack_available(org_id, user_id)
    return [
        cls() for name, cls in ACTIONS.items()
        if name in enabled and cls.provider in providers
    ]


def slack_available(org_id: str, user_id: str, slack_user: str | None = None) -> set[str]:
    """``{"slack"}`` when the bot can post for this person: the company has a
    Slack connection (company-wide or a space's: it is the same bot in the same
    Slack) and we know the person's Slack account (from the event, or an
    identity Handbook already linked)."""
    from ..db.connection import get_connection

    with get_connection() as conn:
        connected = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM oauth_connections WHERE org_id = %s::uuid "
            "AND provider = 'slack' AND NOT needs_reauth)",
            (org_id,),
        ).fetchone()[0]
    return {"slack"} if connected and (slack_user or slack_user_for(org_id, user_id)) else set()


def slack_user_for(org_id: str, user_id: str) -> str | None:
    """The person's Slack user id, as Handbook linked it."""
    from ..db.connection import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT external_id FROM person_identities WHERE org_id = %s::uuid "
            "AND provider = 'slack' AND user_id = %s::uuid ORDER BY last_seen DESC LIMIT 1",
            (org_id, user_id),
        ).fetchone()
    return row[0] if row else None


def get_action(name: str, org_id: str | None = None) -> Action | None:
    """The action for a stored row's name, or ``None`` for an unknown one. An
    MCP tool (``mcp:<server>:<tool>``) is read from that org's server as it is now."""
    if name.startswith("mcp:"):
        from .mcp import get_mcp_action

        return get_mcp_action(name, org_id)
    cls = ACTIONS.get(name)
    return cls() if cls else None
