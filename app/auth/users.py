"""Application users (Phase 13).

A user is always looked up/created by email. ``org_id`` is only ever set when
an org has already been resolved — either an admin's own org at signup, or a
specific email an admin directly invited into their org (``invite_member``) —
a user with no ``org_id`` exists but is NEVER issued a session (see
``app/api/auth.py``), so there is no authenticated state that lacks a tenant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core.exceptions import AuthError
from ..db.connection import get_connection

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"


@dataclass(frozen=True)
class User:
    id: str
    email: str
    org_id: str | None
    role: str
    created_at: datetime


def _row_to_user(row) -> User:
    return User(id=row[0], email=row[1], org_id=row[2], role=row[3], created_at=row[4])


_SELECT_COLUMNS = "id::text, email, org_id::text, role, created_at"


def get_user_by_email(email: str) -> User | None:
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM users WHERE email = %s", (email.lower(),)
        ).fetchone()
    return _row_to_user(row) if row else None


def get_user(user_id: str) -> User | None:
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    return _row_to_user(row) if row else None


def create_user(email: str, *, org_id: str | None = None, role: str = ROLE_MEMBER) -> User:
    with get_connection() as conn:
        row = conn.execute(
            f"INSERT INTO users (email, org_id, role) VALUES (%s, %s, %s) "
            f"RETURNING {_SELECT_COLUMNS}",
            (email.lower(), org_id, role),
        ).fetchone()
    return _row_to_user(row)


def create_admin(email: str, org_id: str) -> User:
    """Create the first admin user for a freshly-created org (Phase 13/14 signup)."""
    return create_user(email, org_id=org_id, role=ROLE_ADMIN)


def invite_member(email: str, org_id: str) -> User:
    """Create a member account for ``email``, scoped directly to ``org_id``.

    Replaces domain-based auto-join: an admin names the exact email to invite,
    no domain matching involved. Doesn't check for an existing account —
    callers (the invite endpoint) do that, mirroring signup's duplicate check,
    so the "already exists" message stays consistent across both entry points.
    """
    return create_user(email, org_id=org_id, role=ROLE_MEMBER)


def list_members(org_id: str) -> list[User]:
    """All users (admin + invited members) belonging to ``org_id``."""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM users WHERE org_id = %s ORDER BY created_at",
            (org_id,),
        ).fetchall()
    return [_row_to_user(row) for row in rows]


def revoke_user_sessions(user_id: str, org_id: str) -> None:
    """Invalidate all outstanding sessions for ``user_id`` in ``org_id`` (Phase 21)."""
    with get_connection() as conn:
        row = conn.execute(
            """
            UPDATE users
            SET sessions_revoked_at = now()
            WHERE id = %s::uuid AND org_id = %s::uuid
            RETURNING id
            """,
            (user_id, org_id),
        ).fetchone()
    if row is None:
        raise ValueError("User not found in this organization")


def _admin_count(conn, org_id: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM users WHERE org_id = %s::uuid AND role = %s",
        (org_id, ROLE_ADMIN),
    ).fetchone()[0]


def _resolve_org_user(conn, user_id: str, org_id: str) -> tuple[str, str]:
    """Return ``(role, email)`` for ``user_id`` in ``org_id``, or raise."""
    row = conn.execute(
        "SELECT role, email FROM users WHERE id = %s::uuid AND org_id = %s::uuid",
        (user_id, org_id),
    ).fetchone()
    if row is None:
        raise ValueError("User not found in this organization")
    return row[0], row[1]


def promote_to_admin(user_id: str, org_id: str) -> User:
    """Promote an org member to admin (succession / shared admin)."""
    with get_connection() as conn:
        role, _email = _resolve_org_user(conn, user_id, org_id)
        if role == ROLE_ADMIN:
            raise AuthError("That person is already an admin.")
        row = conn.execute(
            f"UPDATE users SET role = %s WHERE id = %s::uuid AND org_id = %s::uuid "
            f"RETURNING {_SELECT_COLUMNS}",
            (ROLE_ADMIN, user_id, org_id),
        ).fetchone()
    return _row_to_user(row)


def demote_to_member(user_id: str, org_id: str) -> User:
    """Demote an org admin to member.

    Refuses when they are the last admin (same guard as ``remove_member``).
    Revokes outstanding sessions so a privilege drop is immediate even for
    callers that somehow still trusted a stale JWT role snapshot.
    """
    with get_connection() as conn:
        role, _email = _resolve_org_user(conn, user_id, org_id)
        if role != ROLE_ADMIN:
            raise AuthError("That person is not an admin.")
        if _admin_count(conn, org_id) <= 1:
            raise AuthError("Cannot demote the last admin of this organization.")
        row = conn.execute(
            f"UPDATE users SET role = %s, sessions_revoked_at = now() "
            f"WHERE id = %s::uuid AND org_id = %s::uuid "
            f"RETURNING {_SELECT_COLUMNS}",
            (ROLE_MEMBER, user_id, org_id),
        ).fetchone()
    return _row_to_user(row)


def _handle_sole_owned_workspaces(conn, user_id: str, org_id: str) -> None:
    """Block remove when the target solely owns a space that still has others.

    Spaces where they are the only member are deleted (would be empty after
    cascade). Spaces with other members but no other owner must transfer
    ownership first — see ``make_workspace_owner``.
    """
    sole_owned = conn.execute(
        """
        SELECT w.id::text, w.name
        FROM workspaces w
        JOIN workspace_members wm
          ON wm.workspace_id = w.id
         AND wm.user_id = %s::uuid
         AND wm.role = 'owner'
        WHERE w.org_id = %s::uuid
          AND NOT EXISTS (
            SELECT 1 FROM workspace_members other_owner
            WHERE other_owner.workspace_id = w.id
              AND other_owner.role = 'owner'
              AND other_owner.user_id <> %s::uuid
          )
        """,
        (user_id, org_id, user_id),
    ).fetchall()
    for workspace_id, name in sole_owned:
        others = conn.execute(
            "SELECT count(*) FROM workspace_members "
            "WHERE workspace_id = %s::uuid AND user_id <> %s::uuid",
            (workspace_id, user_id),
        ).fetchone()[0]
        if others > 0:
            raise AuthError(
                f"Cannot remove this person: they are the only owner of space "
                f"{name!r}. Make another member an owner of that space first."
            )
        # Sole member — drop the space rather than leave an empty orphan.
        conn.execute("DELETE FROM workspaces WHERE id = %s::uuid", (workspace_id,))


def remove_member(user_id: str, org_id: str, *, acting_user_id: str) -> None:
    """Remove an invited member (or another admin) from ``org_id``.

    Guards:
    - cannot remove yourself (use Sign out; transfer admin first via promote)
    - cannot remove the last remaining admin
    - cannot remove a sole workspace owner while other members remain
    - target must belong to this org

    Nulls ``oauth_connections.connected_by_user_id`` first (that FK has no
    ON DELETE clause) so a user who once connected a source can still leave.
    ``workspace_members`` cascades via ON DELETE CASCADE on ``users``.
    """
    if user_id == acting_user_id:
        raise AuthError("You cannot remove your own account.")

    with get_connection() as conn:
        role, _email = _resolve_org_user(conn, user_id, org_id)
        if role == ROLE_ADMIN and _admin_count(conn, org_id) <= 1:
            raise AuthError("Cannot remove the last admin of this organization.")
        _handle_sole_owned_workspaces(conn, user_id, org_id)
        conn.execute(
            "UPDATE oauth_connections SET connected_by_user_id = NULL "
            "WHERE connected_by_user_id = %s::uuid AND org_id = %s::uuid",
            (user_id, org_id),
        )
        conn.execute(
            "DELETE FROM users WHERE id = %s::uuid AND org_id = %s::uuid",
            (user_id, org_id),
        )

