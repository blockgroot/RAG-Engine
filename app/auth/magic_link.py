"""Single-use magic-link tokens for employee login (Phase 13).

Only a SHA-256 hash of the token is ever stored (schema.sql:
``magic_link_tokens.token_hash``) — a DB read alone can never be used to log
in as someone. ``consumed_at`` makes the token single-use even if the raw
value leaks before it expires (e.g. in a mail relay's logs).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from ..config.settings import AuthSettings
from ..core.exceptions import AuthError
from ..db.connection import get_connection

_TOKEN_BYTES = 32


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_magic_link_token(email: str, *, settings: AuthSettings | None = None) -> str:
    """Create and store a new single-use token for ``email``. Returns the raw token."""
    settings = settings or AuthSettings.from_env()
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.magic_link_ttl_minutes
    )
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO magic_link_tokens (token_hash, email, expires_at) "
            "VALUES (%s, %s, %s)",
            (_hash(token), email.lower(), expires_at),
        )
    return token


def consume_magic_link_token(token: str) -> str:
    """Validate and consume a token in one atomic step. Returns the bound email.

    Raises ``AuthError`` if the token is unknown, expired, or already used.
    The consume happens in the same statement as the validity check (``UPDATE
    ... WHERE ... AND consumed_at IS NULL AND expires_at > now() RETURNING``)
    so two concurrent verify requests for the same token can't both succeed.
    """
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE magic_link_tokens SET consumed_at = now() "
            "WHERE token_hash = %s AND consumed_at IS NULL AND expires_at > now() "
            "RETURNING email",
            (_hash(token),),
        ).fetchone()
    if not row:
        raise AuthError("This login link is invalid, expired, or already used.")
    return row[0]
