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
from datetime import datetime

from ..core.exceptions import ConfigurationError
from ..db.connection import get_connection
from ..security import decrypt, encrypt
from .base import OAuthTokens


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
