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

#: Every action that exists, by name. Nothing else can ever be proposed.
ACTIONS: dict[str, type[Action]] = {
    cls.name: cls
    for cls in (
        LinearCreateIssue, LinearComment, GitHubCreateIssue, GitHubComment,
        NotionComment, NotionAppend, NotionCreatePage, DriveCreateDoc,
    )
}


def build_actions(org_id: str, workspace_id: str | None, user_id: str) -> list[Action]:
    settings = ActionsSettings.from_env()
    if not settings.enabled:
        return []
    enabled = policy.enabled_actions(org_id, workspace_id)
    if not enabled:
        return []
    providers = connected_providers(org_id, user_id)
    return [
        cls() for name, cls in ACTIONS.items()
        if name in enabled and cls.provider in providers
    ]


def get_action(name: str) -> Action | None:
    """The action class for a stored row's name, or ``None`` for an unknown one."""
    cls = ACTIONS.get(name)
    return cls() if cls else None
