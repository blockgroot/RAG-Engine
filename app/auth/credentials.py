"""Storage/lookup for ``oauth_connections`` rows (Phase 11).

This is the DB-backed replacement for hand-set ``NOTION_TOKEN_<NAME>`` env
vars: once an admin completes the OAuth "Connect" flow (``app/api/auth.py``),
the resulting tokens are encrypted (``app.security``) and stored here, one row
per ``(org_id, provider)`` (enforced by a UNIQUE constraint in schema.sql — see
CLAUDE.md for why this makes cross-tenant ambiguity structurally impossible).

Deliberately NOT merged with the legacy env-var path
(``NotionSettings.resolve_token``): the two credential sources are kept fully
independent, each always requiring an explicit, unambiguous key (``org_id``
here, ``token_name`` there) — no fallback from one to the other. A prior design
review flagged that a shared/ambiguous fallback is exactly how a cross-org
credential leak would happen; keeping the paths separate avoids that risk
entirely rather than trying to reconcile it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..core.exceptions import ConfigurationError, OAuthReauthRequiredError
from ..db.connection import get_connection
from ..security import decrypt, encrypt
from .base import OAuthTokens

# How far ahead of actual expiry we proactively refresh. Google access tokens
# live ~1h; refreshing 5 minutes early absorbs request latency/clock skew
# without adding a retry loop.
_REFRESH_SAFETY_MARGIN = timedelta(minutes=5)


@dataclass(frozen=True)
class OAuthConnectionInfo:
    """Non-secret metadata about a stored connection (safe to show an admin)."""

    id: str
    provider: str
    external_workspace_id: str
    external_workspace_name: str | None
    created_at: datetime


def save_connection(
    org_id: str,
    provider: str,
    tokens: OAuthTokens,
    *,
    connected_by_user_id: str | None = None,
) -> str:
    """Encrypt and upsert an org's OAuth connection for ``provider``.

    One row per ``(org_id, provider)`` — a second connect for the same
    provider replaces the first (e.g. reconnecting after revoking access).
    """
    access_encrypted = encrypt(tokens.access_token)
    refresh_encrypted = encrypt(tokens.refresh_token) if tokens.refresh_token else None

    with get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO oauth_connections (
                org_id, provider, external_workspace_id, external_workspace_name,
                access_token_encrypted, refresh_token_encrypted, expires_at,
                connected_by_user_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (org_id, provider) DO UPDATE SET
                external_workspace_id   = EXCLUDED.external_workspace_id,
                external_workspace_name = EXCLUDED.external_workspace_name,
                access_token_encrypted  = EXCLUDED.access_token_encrypted,
                refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
                expires_at              = EXCLUDED.expires_at,
                connected_by_user_id    = EXCLUDED.connected_by_user_id
            RETURNING id
            """,
            (
                org_id,
                provider,
                tokens.external_workspace_id,
                tokens.external_workspace_name,
                access_encrypted,
                refresh_encrypted,
                tokens.expires_at,
                connected_by_user_id,
            ),
        ).fetchone()
    return str(row[0])


def get_connection_token(org_id: str, provider: str) -> str:
    """Return the decrypted access token for this org's ``provider`` connection.

    Always scoped by BOTH ``org_id`` and ``provider`` — never provider alone —
    so this can never return another org's token. Raises ``ConfigurationError``
    if no connection exists (the admin hasn't connected this provider yet).
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT access_token_encrypted FROM oauth_connections "
            "WHERE org_id = %s AND provider = %s",
            (org_id, provider),
        ).fetchone()
    if not row:
        raise ConfigurationError(
            f"No {provider!r} connection for this organization. Connect it first."
        )
    return decrypt(row[0])


def get_live_connection_token(org_id: str, provider: str) -> str:
    """Return a valid (refreshed if necessary) access token for this connection.

    Provider-agnostic (CLAUDE.md D10): Notion's tokens never expire, Google's
    expire in ~1h — this is the ONE place that owns "is it still good, and if
    not, can/should we refresh it" so every caller benefits without knowing
    which provider it's talking to.

    - No ``expires_at`` (Notion) or still comfortably valid: return the stored
      access token unchanged, no network call.
    - Within ``_REFRESH_SAFETY_MARGIN`` of expiry (or already past) and a
      refresh token is on file: call ``build_oauth_provider(provider).refresh()``.
      - If refresh isn't supported (``NotImplementedError``), fall back to the
        stored access token unchanged — this provider doesn't need refresh.
      - On success, persist the new access/refresh/expiry and return the new
        access token. A refresh response that omits a new refresh token (Google
        commonly does on non-first refreshes) must NOT null out the one we
        already have on file.
      - On a terminal provider failure (e.g. Google's ``invalid_grant`` from a
        revoked/expired refresh token), raise ``OAuthReauthRequiredError`` so
        the caller can surface an actionable "reconnect" message. Never
        retry-looped — at most one refresh attempt per call.
    - No refresh token on file: nothing to refresh with, return the stored
      (possibly stale) access token as-is.
    """
    # Imported lazily to avoid a hard import cycle (factory -> providers ->
    # credentials isn't a cycle today, but this keeps the module import order
    # from ever mattering here, matching the lazy-import convention used for
    # optional/heavier deps elsewhere in this codebase).
    from .factory import build_oauth_provider

    with get_connection() as conn:
        row = conn.execute(
            "SELECT access_token_encrypted, refresh_token_encrypted, expires_at "
            "FROM oauth_connections WHERE org_id = %s AND provider = %s",
            (org_id, provider),
        ).fetchone()
    if not row:
        raise ConfigurationError(
            f"No {provider!r} connection for this organization. Connect it first."
        )

    access_encrypted, refresh_encrypted, expires_at = row
    access_token = decrypt(access_encrypted)

    if expires_at is None:
        return access_token

    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at - now > _REFRESH_SAFETY_MARGIN:
        return access_token

    if not refresh_encrypted:
        # Expiring/expired but nothing to refresh with — hand back what we
        # have; the caller's downstream API call will fail with its own
        # provider error if it's actually no longer valid.
        return access_token

    refresh_token = decrypt(refresh_encrypted)

    try:
        new_tokens = build_oauth_provider(provider).refresh(refresh_token)
    except NotImplementedError:
        # Provider doesn't support/need refresh (e.g. Notion) — stored token
        # stands unchanged.
        return access_token
    except OAuthReauthRequiredError:
        raise
    except Exception as exc:  # noqa: BLE001 - map any other refresh failure to a terminal error
        raise OAuthReauthRequiredError(
            f"Refreshing the {provider!r} connection failed; reconnect it to continue.",
            cause=exc,
        ) from exc

    new_access_encrypted = encrypt(new_tokens.access_token)
    # Don't overwrite a stored refresh token with None — Google frequently
    # omits it on refresh responses after the first exchange.
    new_refresh_encrypted = (
        encrypt(new_tokens.refresh_token) if new_tokens.refresh_token else refresh_encrypted
    )

    with get_connection() as conn:
        conn.execute(
            "UPDATE oauth_connections SET "
            "access_token_encrypted = %s, refresh_token_encrypted = %s, expires_at = %s "
            "WHERE org_id = %s AND provider = %s",
            (
                new_access_encrypted,
                new_refresh_encrypted,
                new_tokens.expires_at,
                org_id,
                provider,
            ),
        )

    return new_tokens.access_token


def list_connections(org_id: str) -> list[OAuthConnectionInfo]:
    """List this org's connections (metadata only — never the decrypted token)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id::text, provider, external_workspace_id, "
            "external_workspace_name, created_at FROM oauth_connections "
            "WHERE org_id = %s ORDER BY created_at DESC",
            (org_id,),
        ).fetchall()
    return [
        OAuthConnectionInfo(
            id=r[0],
            provider=r[1],
            external_workspace_id=r[2],
            external_workspace_name=r[3],
            created_at=r[4],
        )
        for r in rows
    ]
