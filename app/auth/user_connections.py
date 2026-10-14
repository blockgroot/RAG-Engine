"""A person's own token for acting in a tool (Act mode).

Separate from ``credentials.py`` on purpose. Those rows belong to a scope (the
org or a space), were connected by an admin or owner, and serve reads and
sync. These belong to one person, were granted by that person on the tool's
own consent page, and serve only that person's approved writes -- so a write
runs with their rights in the tool, and the tool shows them as its author.

Keyed by ``(org_id, user_id, provider)`` in every query: one person's token is
never returned for another, nor across orgs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..core.exceptions import ConfigurationError, OAuthError, OAuthReauthRequiredError
from ..db.connection import get_connection
from ..security import decrypt, encrypt


@dataclass(frozen=True)
class UserTokens:
    """What a per-person OAuth exchange returns, before encryption.

    ``scopes`` are the GRANTED ones: Google lets people untick a scope, so
    what was asked for is not proof of what was given.
    """

    access_token: str
    external_user_id: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    external_user_name: str | None = None
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class UserConnectionInfo:
    """Non-secret facts about a person's connection, safe to show them."""

    provider: str
    external_user_id: str
    external_user_name: str | None
    scopes: tuple[str, ...]
    needs_reauth: bool
    created_at: datetime


@dataclass(frozen=True)
class StoredUserToken:
    """A decrypted token with what a refresh needs. Never leaves the server."""

    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: tuple[str, ...]


def save_user_connection(org_id: str, user_id: str, provider: str, tokens: UserTokens) -> str:
    """Encrypt and upsert. Reconnecting replaces the token and clears a
    reconnect flag, like an admin reconnecting a source."""
    with get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO user_connections
                (org_id, user_id, provider, external_user_id, external_user_name,
                 scopes, access_token_encrypted, refresh_token_encrypted, expires_at)
            VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (org_id, user_id, provider) DO UPDATE SET
                external_user_id        = EXCLUDED.external_user_id,
                external_user_name      = EXCLUDED.external_user_name,
                scopes                  = EXCLUDED.scopes,
                access_token_encrypted  = EXCLUDED.access_token_encrypted,
                refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
                expires_at              = EXCLUDED.expires_at,
                needs_reauth            = false
            RETURNING id::text
            """,
            (
                org_id, user_id, provider, tokens.external_user_id,
                tokens.external_user_name, list(tokens.scopes),
                encrypt(tokens.access_token),
                encrypt(tokens.refresh_token) if tokens.refresh_token else None,
                tokens.expires_at,
            ),
        ).fetchone()
    return row[0]


def get_user_token(org_id: str, user_id: str, provider: str) -> StoredUserToken:
    """This person's token for ``provider``.

    Raises ``ConfigurationError`` when they never connected it and
    ``OAuthReauthRequiredError`` when it stopped working -- two different
    things to tell them ("Connect" vs "Reconnect").
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT access_token_encrypted, refresh_token_encrypted, expires_at, "
            "scopes, needs_reauth FROM user_connections "
            "WHERE org_id = %s::uuid AND user_id = %s::uuid AND provider = %s",
            (org_id, user_id, provider),
        ).fetchone()
    if not row:
        raise ConfigurationError(f"Connect your {provider} account to let Handbook act for you.")
    if row[4]:
        raise OAuthReauthRequiredError(f"Reconnect your {provider} account.")
    return StoredUserToken(
        access_token=decrypt(row[0]),
        refresh_token=decrypt(row[1]) if row[1] else None,
        expires_at=row[2],
        scopes=tuple(row[3] or ()),
    )


#: Refresh this long before expiry, so a write never starts on a dying token.
REFRESH_MARGIN = timedelta(minutes=5)


def fresh_user_token(org_id: str, user_id: str, provider: str) -> str:
    """A working access token for this person, refreshed first if it is near expiry.

    The refresh runs under a row lock and saves the new pair in the same
    transaction: GitHub's refresh token is single-use ("that refresh token and
    the old user access token will no longer work"), so two requests that both
    refreshed would leave the connection broken. The second waits on the lock
    and then finds a fresh token. A refresh that fails marks the connection
    for reconnecting, and raises ``OAuthReauthRequiredError``.
    """
    stored = get_user_token(org_id, user_id, provider)
    if stored.expires_at is None or stored.expires_at - REFRESH_MARGIN > datetime.now(timezone.utc):
        return stored.access_token

    from .factory import build_oauth_provider

    failed: Exception | None = None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT access_token_encrypted, refresh_token_encrypted, expires_at "
            "FROM user_connections WHERE org_id = %s::uuid AND user_id = %s::uuid "
            "AND provider = %s FOR UPDATE",
            (org_id, user_id, provider),
        ).fetchone()
        if row is None:
            raise ConfigurationError(f"Connect your {provider} account to let Handbook act for you.")
        if row[2] is not None and row[2] - REFRESH_MARGIN > datetime.now(timezone.utc):
            return decrypt(row[0])  # another request refreshed it while we waited
        try:
            if not row[1]:
                raise OAuthReauthRequiredError("no refresh token")
            tokens = build_oauth_provider(provider).refresh(decrypt(row[1]))
        except (OAuthError, NotImplementedError) as exc:
            failed = exc
        else:
            conn.execute(
                "UPDATE user_connections SET access_token_encrypted = %s, "
                "refresh_token_encrypted = COALESCE(%s, refresh_token_encrypted), "
                "expires_at = %s WHERE org_id = %s::uuid AND user_id = %s::uuid AND provider = %s",
                (encrypt(tokens.access_token),
                 encrypt(tokens.refresh_token) if tokens.refresh_token else None,
                 tokens.expires_at, org_id, user_id, provider),
            )
            return tokens.access_token
    mark_user_needs_reauth(org_id, user_id, provider)
    raise OAuthReauthRequiredError(f"Reconnect your {provider} account.", cause=failed) from failed


def mark_user_needs_reauth(org_id: str, user_id: str, provider: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE user_connections SET needs_reauth = true "
            "WHERE org_id = %s::uuid AND user_id = %s::uuid AND provider = %s",
            (org_id, user_id, provider),
        )


def list_user_connections(org_id: str, user_id: str) -> list[UserConnectionInfo]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT provider, external_user_id, external_user_name, scopes, "
            "needs_reauth, created_at FROM user_connections "
            "WHERE org_id = %s::uuid AND user_id = %s::uuid ORDER BY provider",
            (org_id, user_id),
        ).fetchall()
    return [
        UserConnectionInfo(r[0], r[1], r[2], tuple(r[3] or ()), r[4], r[5]) for r in rows
    ]


def connected_providers(org_id: str, user_id: str) -> set[str]:
    """Providers this person can act in right now (connected, not broken)."""
    return {c.provider for c in list_user_connections(org_id, user_id) if not c.needs_reauth}


def delete_user_connection(org_id: str, user_id: str, provider: str) -> bool:
    """Forget a person's token. Returns whether there was one. Revoking it in
    the tool's own settings is theirs to do; this stops Handbook using it."""
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM user_connections "
            "WHERE org_id = %s::uuid AND user_id = %s::uuid AND provider = %s",
            (org_id, user_id, provider),
        )
        return (cur.rowcount or 0) > 0
