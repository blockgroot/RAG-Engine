"""Changing a member's sign-in email without losing their document access.

Access matches on email, and sources keep listing the old address, so a
change keeps it as an alias (`user_email_aliases`) that `Viewer.acl()` emits.
Safe because every alias was proven (a confirmed change from a verified
login), and an address belongs to one person at a time: an alias another
`users` row signs in with is ignored, and a change to it drops it as an alias.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config.settings import AuthSettings
from ..core.exceptions import AuthError
from ..db.connection import get_connection

_TOKEN_BYTES = 32


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _clean(email: str) -> str:
    return (email or "").strip().lower()


@dataclass(frozen=True)
class PendingChange:
    user_id: str
    old_email: str
    new_email: str


def request_email_change(
    user_id: str, new_email: str, *, settings: AuthSettings | None = None
) -> str:
    """Store a pending change; return the raw token to mail to ``new_email``.

    Refuses an address another account signs in with: two logins on one address
    make `get_user_by_email` ambiguous.
    """
    email = _clean(new_email)
    if "@" not in email:
        raise AuthError("A valid email is required.")
    settings = settings or AuthSettings.from_env()
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.magic_link_ttl_minutes
    )
    with get_connection() as conn:
        row = conn.execute(
            "SELECT lower(email) FROM users WHERE id = %s::uuid", (user_id,)
        ).fetchone()
        if row is None:
            raise AuthError("No such account.")
        if row[0] == email:
            raise AuthError("That is already your email.")
        taken = conn.execute(
            "SELECT 1 FROM users WHERE lower(email) = %s", (email,)
        ).fetchone()
        if taken:
            raise AuthError("Another account already uses that email.")
        # One pending change per person: a newer request replaces the older one,
        # so an old link in someone's inbox cannot be confirmed after a retry.
        conn.execute(
            "DELETE FROM email_change_requests WHERE user_id = %s::uuid AND consumed_at IS NULL",
            (user_id,),
        )
        conn.execute(
            "INSERT INTO email_change_requests (token_hash, user_id, new_email, expires_at) "
            "VALUES (%s, %s::uuid, %s, %s)",
            (_hash(token), user_id, email, expires_at),
        )
    return token


def peek_email_change(token: str) -> PendingChange | None:
    """The change a link would make, without making it (the GET page).

    A mail scanner that follows the link must not confirm it.
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT r.user_id::text, u.email, r.new_email
              FROM email_change_requests r JOIN users u ON u.id = r.user_id
             WHERE r.token_hash = %s AND r.consumed_at IS NULL AND r.expires_at > now()
            """,
            (_hash(token),),
        ).fetchone()
    return PendingChange(row[0], row[1], row[2]) if row else None


def confirm_email_change(token: str) -> PendingChange:
    """Apply the change in one transaction: swap the login, keep the old address.

    A failure half-way leaves the old address and a usable link, never a login with no alias.
    """
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE email_change_requests SET consumed_at = now() "
            "WHERE token_hash = %s AND consumed_at IS NULL AND expires_at > now() "
            "RETURNING user_id::text, new_email",
            (_hash(token),),
        ).fetchone()
        if row is None:
            raise AuthError("This link is invalid, expired, or already used.")
        user_id, new_email = row
        current = conn.execute(
            "SELECT lower(email) FROM users WHERE id = %s::uuid FOR UPDATE", (user_id,)
        ).fetchone()
        if current is None:
            raise AuthError("That account no longer exists.")
        old_email = current[0]
        taken = conn.execute(
            "SELECT 1 FROM users WHERE lower(email) = %s AND id <> %s::uuid",
            (new_email, user_id),
        ).fetchone()
        if taken:
            # Raising rolls the consume back too, so the person is not left
            # holding a spent link for a change that never happened.
            raise AuthError("Another account started using that email in the meantime.")
        conn.execute(
            "UPDATE users SET email = %s WHERE id = %s::uuid", (new_email, user_id)
        )
        # The new address is now somebody's LOGIN, so it is nobody's alias.
        conn.execute("DELETE FROM user_email_aliases WHERE email = %s", (new_email,))
        conn.execute(
            "INSERT INTO user_email_aliases (email, user_id) VALUES (%s, %s::uuid) "
            "ON CONFLICT (email) DO UPDATE SET user_id = EXCLUDED.user_id, created_at = now()",
            (old_email, user_id),
        )
    return PendingChange(user_id, old_email, new_email)


def prior_emails(user_id: str) -> list[str]:
    """This person's prior addresses, for the account page."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT email FROM user_email_aliases WHERE user_id = %s::uuid ORDER BY created_at",
            (user_id,),
        ).fetchall()
    return [r[0] for r in rows]


def remove_prior_email(user_id: str, email: str) -> bool:
    """Drop one prior address. Only its owner can; returns whether it existed."""
    with get_connection() as conn:
        deleted = conn.execute(
            "DELETE FROM user_email_aliases WHERE user_id = %s::uuid AND email = %s",
            (user_id, _clean(email)),
        ).rowcount
    return bool(deleted)


def aliases_for(org_id: str, email: str) -> tuple[str, ...]:
    """Prior addresses of the member signing in as ``email`` in ``org_id``.

    Pinned to the org; an alias someone else now signs in with is skipped (it is theirs).
    """
    address = _clean(email)
    if not address:
        return ()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.email
              FROM user_email_aliases a
              JOIN users u ON u.id = a.user_id
             WHERE lower(u.email) = %s AND u.org_id = %s::uuid
               AND NOT EXISTS (SELECT 1 FROM users o WHERE lower(o.email) = a.email)
             ORDER BY a.email
            """,
            (address, org_id),
        ).fetchall()
    return tuple(r[0] for r in rows)
