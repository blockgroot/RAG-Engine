"""Single-use magic-link tokens for employee login.

Only the SHA-256 of a token is stored, so a DB read cannot log anyone in;
``consumed_at`` keeps a leaked token single-use.
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
    """Validate and consume a token in one statement; return its email.

    One conditional UPDATE, so two concurrent verifies cannot both succeed.
    Raises ``AuthError`` if unknown, expired or used.
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
