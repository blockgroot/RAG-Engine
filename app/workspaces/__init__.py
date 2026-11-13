"""Sub-workspace (space) CRUD + membership. One backend, so no base.py/factory."""

from __future__ import annotations

from .store import (
    WorkspaceInfo,
    assert_member,
    create_workspace,
    invite_member,
    list_my_workspaces,
    list_workspace_members,
    make_workspace_owner,
    remove_workspace_member,
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
    "remove_workspace_member",
    "delete_workspace",
]
