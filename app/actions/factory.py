"""Build the actions this person may be offered right now.

The registry is the whole menu: an action that is not a class here cannot be
proposed, whatever the model asks for. Offered means all three hold -- Act is
on (``ACTIONS_ENABLED``), the org switched the action on and this space did
not switch it off, and the person connected that tool themselves.
"""

from __future__ import annotations

from ..auth.user_connections import connected_providers
from ..config.settings import ActionsSettings
from . import policy
from .base import Action
from .drive import DriveCreateDoc
from .github import GitHubComment, GitHubCreateIssue
from .linear import LinearComment, LinearCreateIssue
from .notion import NotionAppend, NotionComment, NotionCreatePage
from .slack import SlackPostMessage

#: Every action that exists, by name. Nothing else can ever be proposed.
ACTIONS: dict[str, type[Action]] = {
    cls.name: cls
    for cls in (
        LinearCreateIssue, LinearComment, GitHubCreateIssue, GitHubComment,
        NotionComment, NotionAppend, NotionCreatePage, DriveCreateDoc,
        SlackPostMessage,
    )
}


def build_actions(org_id: str, workspace_id: str | None, user_id: str) -> list[Action]:
    settings = ActionsSettings.from_env()
    if not settings.enabled:
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
    """``{"slack"}`` when the bot can post for this person: the company has an
    org-wide Slack connection and we know the person's Slack account (from the
    event, or an identity Handbook already linked)."""
    from ..db.connection import get_connection

    with get_connection() as conn:
        connected = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM oauth_connections WHERE org_id = %s::uuid "
            "AND provider = 'slack' AND workspace_id IS NULL AND NOT needs_reauth)",
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


def get_action(name: str) -> Action | None:
    """The action class for a stored row's name, or ``None`` for an unknown one."""
    cls = ACTIONS.get(name)
    return cls() if cls else None
