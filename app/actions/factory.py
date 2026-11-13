"""The actions this person may be offered now: MCP tools only, where Agent mode
is allowed, the server is on, the tool is "write" or "lookup", and the person
signed in to that server.
"""

from __future__ import annotations

from . import policy
from .base import Action


def build_actions(org_id: str, workspace_id: str | None, user_id: str) -> list[Action]:
    if not policy.act_on(org_id, workspace_id):
        return []
    from .mcp import mcp_actions

    return mcp_actions(org_id, user_id, workspace_id)


def get_action(name: str, org_id: str | None = None) -> Action | None:
    """The action for a stored row's name (``mcp:<server>:<tool>``), as the
    server is now; ``None`` for anything else, including a retired built-in."""
    if not name.startswith("mcp:"):
        return None
    from .mcp import get_mcp_action

    return get_mcp_action(name, org_id)
