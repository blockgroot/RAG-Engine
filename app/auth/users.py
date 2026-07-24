"""Application users (Phase 13).

A user is always looked up/created by email. ``org_id`` is only ever set when
an org has already been resolved (via a verified, auto-join-enabled domain, or
by an admin's own org at signup) — a user with no ``org_id`` exists but is
NEVER issued a session (see ``app/api/auth.py``), so there is no authenticated
state that lacks a tenant.
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


def get_or_create_member(email: str, org_id: str) -> User:
    """Return the existing user for ``email``, or create one scoped to ``org_id``.

    Used by the magic-link request flow once a verified, auto-join-enabled
    domain has resolved which org this email belongs to. Never changes an
    existing user's ``org_id`` — an email is bound to its first-resolved org
    for good, so a later domain change can't silently move someone's account
    into a different tenant.
    """
    existing = get_user_by_email(email)
    if existing is not None:
        return existing
    return create_user(email, org_id=org_id, role=ROLE_MEMBER)


def create_admin(email: str, org_id: str) -> User:
    """Create the first admin user for a freshly-created org (Phase 13/14 signup)."""
    return create_user(email, org_id=org_id, role=ROLE_ADMIN)
