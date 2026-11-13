"""Sub-workspace CRUD + membership.

A space belongs to one org; its members are a subset of that org's users.
``assert_member`` is the only place a ``workspace_id`` is validated against a
user: never trust one without it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.exceptions import AuthError, NotFoundError
from ..db.connection import get_connection


@dataclass(frozen=True)
class WorkspaceInfo:
    id: str
    org_id: str
    name: str
    created_by: str | None
    role: str | None = None  # populated when listing "my workspaces"


def create_workspace(org_id: str, name: str, created_by_user_id: str) -> str:
    """Create a sub-workspace and add its creator as ``owner``."""
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO workspaces (org_id, name, created_by) VALUES (%s, %s, %s) "
            "RETURNING id::text",
            (org_id, name, created_by_user_id),
        ).fetchone()
        workspace_id = row[0]
        conn.execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role, invited_by) "
            "VALUES (%s, %s, 'owner', %s)",
            (workspace_id, created_by_user_id, created_by_user_id),
        )
    return workspace_id


def invite_member(workspace_id: str, org_id: str, inviter_user_id: str, invitee_email: str) -> None:
    """Add an existing org user (by email) to the workspace.

    The invitee must already be a user of this org, so a space never crosses
    the tenant boundary. Raises ``NotFoundError`` otherwise; never creates a
    user (that is invite's job).
    """
    with get_connection() as conn:
        user_row = conn.execute(
            "SELECT id::text FROM users WHERE email = %s AND org_id = %s",
            (invitee_email.lower(), org_id),
        ).fetchone()
        if not user_row:
            raise NotFoundError(f"{invitee_email!r} is not a member of this organization")
        conn.execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role, invited_by) "
            "VALUES (%s, %s, 'member', %s) ON CONFLICT (workspace_id, user_id) DO NOTHING",
            (workspace_id, user_row[0], inviter_user_id),
        )


def assert_member(workspace_id: str, org_id: str, user_id: str) -> str:
    """Return the caller's role in this workspace, or raise ``AuthError``.

    Checks ``org_id`` too, so a forged id from another org fails closed. Call
    before any read or write scoped to a workspace.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT wm.role FROM workspace_members wm "
            "JOIN workspaces w ON w.id = wm.workspace_id "
            "WHERE wm.workspace_id = %s AND wm.user_id = %s AND w.org_id = %s",
            (workspace_id, user_id, org_id),
        ).fetchone()
    if not row:
        raise AuthError("Not a member of this workspace")
    return row[0]


def list_my_workspaces(org_id: str, user_id: str) -> list[WorkspaceInfo]:
    """Workspaces ``user_id`` is a member of, scoped to ``org_id``."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT w.id::text, w.org_id::text, w.name, w.created_by::text, wm.role "
            "FROM workspaces w JOIN workspace_members wm ON wm.workspace_id = w.id "
            "WHERE w.org_id = %s AND wm.user_id = %s ORDER BY w.created_at DESC",
            (org_id, user_id),
        ).fetchall()
    return [
        WorkspaceInfo(id=r[0], org_id=r[1], name=r[2], created_by=r[3], role=r[4]) for r in rows
    ]


def get_workspace_name(org_id: str, workspace_id: str) -> str | None:
    """Display name for one workspace, or None if it is not in this org.

    No membership check: only labels a scope the caller already authorized.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT name FROM workspaces WHERE id = %s AND org_id = %s",
            (workspace_id, org_id),
        ).fetchone()
    return row[0] if row else None


def list_workspace_members(workspace_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT u.id::text, u.email, wm.role, wm.joined_at FROM workspace_members wm "
            "JOIN users u ON u.id = wm.user_id WHERE wm.workspace_id = %s ORDER BY wm.joined_at",
            (workspace_id,),
        ).fetchall()
    return [
        {"user_id": r[0], "email": r[1], "role": r[2], "joined_at": r[3]} for r in rows
    ]


def make_workspace_owner(
    workspace_id: str, org_id: str, acting_user_id: str, target_user_id: str
) -> None:
    """Promote an existing workspace member to ``owner`` (additive), so a
    departing sole owner can then be removed.
    """
    acting_role = assert_member(workspace_id, org_id, acting_user_id)
    if acting_role != "owner":
        raise AuthError("Only a space owner can make someone else an owner")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT role FROM workspace_members "
            "WHERE workspace_id = %s::uuid AND user_id = %s::uuid",
            (workspace_id, target_user_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("That person is not a member of this space")
        if row[0] == "owner":
            raise AuthError("That person is already an owner of this space")
        # Confirm target is still in the same org (membership alone shouldn't
        # outlive an org boundary, but pair the check with org_id explicitly).
        org_row = conn.execute(
            "SELECT 1 FROM users WHERE id = %s::uuid AND org_id = %s::uuid",
            (target_user_id, org_id),
        ).fetchone()
        if org_row is None:
            raise NotFoundError("That person is not a member of this organization")
        conn.execute(
            "UPDATE workspace_members SET role = 'owner' "
            "WHERE workspace_id = %s::uuid AND user_id = %s::uuid",
            (workspace_id, target_user_id),
        )


def remove_workspace_member(workspace_id: str, org_id: str, acting_user_id: str, target_user_id: str) -> None:
    """Remove a member from a workspace (owner-only). Never touches the org account.

    Refuses the sole owner: promote another first, or delete the space.
    """
    acting_role = assert_member(workspace_id, org_id, acting_user_id)
    if acting_role != "owner":
        raise AuthError("Only a space owner can remove a member")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT role FROM workspace_members "
            "WHERE workspace_id = %s::uuid AND user_id = %s::uuid",
            (workspace_id, target_user_id),
        ).fetchone()
        if row is None:
            raise NotFoundError("That person is not a member of this space")
        if row[0] == "owner":
            other_owners = conn.execute(
                "SELECT 1 FROM workspace_members "
                "WHERE workspace_id = %s::uuid AND role = 'owner' AND user_id != %s::uuid",
                (workspace_id, target_user_id),
            ).fetchone()
            if other_owners is None:
                raise AuthError(
                    "Can't remove the only owner — make someone else an owner first, "
                    "or delete the space instead"
                )
        conn.execute(
            "DELETE FROM workspace_members WHERE workspace_id = %s::uuid AND user_id = %s::uuid",
            (workspace_id, target_user_id),
        )


def delete_workspace(workspace_id: str, org_id: str, acting_user_id: str) -> None:
    """Permanently delete a space and all of its scoped content (owner-only).

    Paired with ``org_id`` so another tenant's id cannot match; everything
    scoped to it cascades.
    """
    role = assert_member(workspace_id, org_id, acting_user_id)
    if role != "owner":
        raise AuthError("Only a space owner can delete this space")

    with get_connection() as conn:
        row = conn.execute(
            "DELETE FROM workspaces WHERE id = %s::uuid AND org_id = %s::uuid "
            "RETURNING id",
            (workspace_id, org_id),
        ).fetchone()
    if row is None:
        raise NotFoundError("Workspace not found")

    # Installation tokens are process-local; CASCADE drops the DB row but not
    # the in-memory cache entry for this workspace scope.
    try:
        from ..auth.credentials import clear_installation_token_cache

        clear_installation_token_cache(org_id, workspace_id)
    except Exception:  # noqa: BLE001 - cache clear must not undo a successful delete
        pass

