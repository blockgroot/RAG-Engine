"""Sub-workspace ("workspace-within-a-workspace") CRUD + membership.

Deliberately a single-implementation package like ``app/jobs/`` — no
``base.py``/factory, since there is exactly one storage backend (Postgres)
and no second one planned; see CLAUDE.md's convention that only genuinely
swappable capabilities get an interface.
"""

from __future__ import annotations

from .store import (
    WorkspaceInfo,
    assert_member,
    create_workspace,
    invite_member,
    list_my_workspaces,
    list_workspace_members,
    make_workspace_owner,
    delete_workspace,
)

__all__ = [
    "WorkspaceInfo",
    "assert_member",
    "create_workspace",
    "invite_member",
    "list_my_workspaces",
    "list_workspace_members",
    "make_workspace_owner",
    "delete_workspace",
]
