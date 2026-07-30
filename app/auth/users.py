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
