"""Owner-email whitelist: who may self-serve ``POST /auth/signup``.

DB-backed (``owner_email_whitelist``) rather than an env var, so granting a
new owner takes effect immediately with no redeploy — managed exclusively
via ``scripts/manage_owner_whitelist.py`` (no HTTP/session surface, matching
this app's existing platform-operator-action pattern).
"""

from __future__ import annotations

from ..db.connection import get_connection


def is_whitelisted(email: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM owner_email_whitelist WHERE email = %s", (email.lower(),)
        ).fetchone()
    return row is not None


def add_owner_email(email: str) -> None:
    """Idempotent: adding an already-whitelisted email is a harmless no-op."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO owner_email_whitelist (email) VALUES (%s) "
            "ON CONFLICT (email) DO NOTHING",
            (email.lower(),),
        )


def remove_owner_email(email: str) -> None:
    """Idempotent: removing an email that isn't whitelisted is a no-op."""
    with get_connection() as conn:
        conn.execute("DELETE FROM owner_email_whitelist WHERE email = %s", (email.lower(),))


def list_owner_emails() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT email FROM owner_email_whitelist ORDER BY created_at"
        ).fetchall()
    return [row[0] for row in rows]
