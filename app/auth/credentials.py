"""Storage and lookup for ``oauth_connections`` rows (encrypted tokens).

Kept apart from the env-var path (``NotionSettings.resolve_token``) with no
fallback either way: each needs an explicit key, because an ambiguous
fallback is how one org's credential reaches another.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Json

from ..core.exceptions import ConfigurationError, OAuthReauthRequiredError
from ..db.connection import get_connection
from ..security import decrypt, encrypt
from .base import OAuthTokens

# Refresh this early to absorb latency and clock skew without a retry loop.
_REFRESH_SAFETY_MARGIN = timedelta(minutes=5)

# Process-local cache of GitHub installation tokens (valid an hour), keyed by
# ``(org_id, workspace_id, installation_id)`` so a hit can never cross tenants.
# Not in Postgres: it holds live credentials and is cheap to rebuild.
_INSTALLATION_TOKEN_CACHE: dict[tuple[str, str | None, str], tuple[str, datetime]] = {}


@dataclass(frozen=True)
class OAuthConnectionInfo:
    """Non-secret metadata about a stored connection (safe to show an admin)."""

    id: str
    provider: str
    external_workspace_id: str
    external_workspace_name: str | None
    created_at: datetime
    # Provider-specific ingestion scope (e.g. Google's folder_id/folder_name).
    # Never secrets — safe to return on the admin connections list.
    source_config: dict | None = None
    # Sticky reconnect signal (set on terminal auth failure; cleared on reconnect
    # or a successful live provider call). Safe / non-secret.
    needs_reauth: bool = False
    reauth_reason: str | None = None
    # When the background sync last ATTEMPTED this connection: the only
    # freshness signal on the card, so a working sync is told from a broken one.
    last_sync_at: datetime | None = None


def save_connection(
    org_id: str,
    provider: str,
    tokens: OAuthTokens,
    *,
    connected_by_user_id: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Encrypt and upsert a connection for ``provider``.

    One row per ``(org_id, provider)`` when ``workspace_id`` is ``None``, else
    per ``(org_id, provider, workspace_id)``. Two partial unique indexes, and
    ``ON CONFLICT`` must match the partial index's predicate, so two queries.
    """
    access_encrypted = encrypt(tokens.access_token)
    refresh_encrypted = encrypt(tokens.refresh_token) if tokens.refresh_token else None
    conflict_clause = (
        "ON CONFLICT (org_id, provider) WHERE workspace_id IS NULL DO UPDATE SET"
        if workspace_id is None
        else "ON CONFLICT (org_id, provider, workspace_id) WHERE workspace_id IS NOT NULL DO UPDATE SET"
    )

    with get_connection() as conn:
        row = conn.execute(
            f"""
            INSERT INTO oauth_connections (
                org_id, provider, external_workspace_id, external_workspace_name,
                access_token_encrypted, refresh_token_encrypted, expires_at,
                connected_by_user_id, workspace_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            {conflict_clause}
                external_workspace_id   = EXCLUDED.external_workspace_id,
                external_workspace_name = EXCLUDED.external_workspace_name,
                access_token_encrypted  = EXCLUDED.access_token_encrypted,
                refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
                expires_at              = EXCLUDED.expires_at,
                connected_by_user_id    = EXCLUDED.connected_by_user_id,
                needs_reauth            = false,
                reauth_reason           = NULL
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
                workspace_id,
            ),
        ).fetchone()
    return str(row[0])


def get_connection_token(
    org_id: str, provider: str, workspace_id: str | None = None
) -> str:
    """Decrypted access token for this connection, scoped by org, provider and workspace.

    Raises ``ConfigurationError`` when it is not connected.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT access_token_encrypted FROM oauth_connections "
            "WHERE org_id = %s AND provider = %s AND workspace_id IS NOT DISTINCT FROM %s",
            (org_id, provider, workspace_id),
        ).fetchone()
    if not row:
        raise ConfigurationError(
            f"No {provider!r} connection for this organization. Connect it first."
        )
    return decrypt(row[0])


def get_live_connection_token(
    org_id: str, provider: str, workspace_id: str | None = None
) -> str:
    """A valid access token for this connection, refreshed if necessary.

    The one place that decides refresh, for every provider. No expiry or still
    valid: returned as is. Near expiry with a refresh token: refreshed once
    (no loop) and saved, keeping the old refresh token if none came back.
    Refresh unsupported or no refresh token: the stored token. A terminal
    failure raises ``OAuthReauthRequiredError``.
    """
    # Lazy import keeps module import order irrelevant.
    from .factory import build_oauth_provider

    # GitHub: callers need an installation token minted from the App key, not
    # the stored user token.
    if provider == "github":
        return _github_installation_token(org_id, workspace_id)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT access_token_encrypted, refresh_token_encrypted, expires_at "
            "FROM oauth_connections "
            "WHERE org_id = %s AND provider = %s AND workspace_id IS NOT DISTINCT FROM %s",
            (org_id, provider, workspace_id),
        ).fetchone()
    if not row:
        raise ConfigurationError(
            f"No {provider!r} connection for this organization. Connect it first."
        )

    access_encrypted, refresh_encrypted, expires_at = row

    from .org_apps import OWN_APP_PROVIDERS, has_app

    if provider in OWN_APP_PROVIDERS and not has_app(org_id, provider):
        # Hosted has no shared Google or Slack app: a connection through the old one
        # stops here with a step the admin can take.
        reason = (
            f"Set up your company's own {'Google' if provider == 'google' else 'Slack'} "
            "app under Sources, then reconnect."
        )
        mark_needs_reauth(org_id, provider, workspace_id, reason)
        raise OAuthReauthRequiredError(reason)

    access_token = decrypt(access_encrypted)

    if expires_at is None:
        return access_token

    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at - now > _REFRESH_SAFETY_MARGIN:
        return access_token

    if not refresh_encrypted:
        # Nothing to refresh with: return what we have; the API call will fail if stale.
        return access_token

    refresh_token = decrypt(refresh_encrypted)

    try:
        new_tokens = build_oauth_provider(provider, org_id=org_id).refresh(refresh_token)
    except NotImplementedError:
        # Provider doesn't support/need refresh (e.g. Notion) — stored token
        # stands unchanged.
        return access_token
    except OAuthReauthRequiredError as exc:
        mark_needs_reauth(org_id, provider, workspace_id, str(exc))
        raise
    except ConfigurationError:
        # This HOST cannot refresh (e.g. a secret unset): that says nothing about the
        # tenant's token, so it is not flagged for reauth.
        raise
    except Exception as exc:  # noqa: BLE001 - map any other refresh failure to a terminal error
        wrapped = OAuthReauthRequiredError(
            f"Refreshing the {provider!r} connection failed; reconnect it to continue.",
            cause=exc,
        )
        mark_needs_reauth(org_id, provider, workspace_id, str(wrapped))
        raise wrapped from exc

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
            "WHERE org_id = %s AND provider = %s AND workspace_id IS NOT DISTINCT FROM %s",
            (
                new_access_encrypted,
                new_refresh_encrypted,
                new_tokens.expires_at,
                org_id,
                provider,
                workspace_id,
            ),
        )

    return new_tokens.access_token


def _github_installation_token(org_id: str, workspace_id: str | None = None) -> str:
    """A valid GitHub installation token for this connection, cached until near expiry.

    ``installation_id`` comes from ``source_config`` (verified at connect).
    Never falls back to the stored user token.
    """
    from .github_app import mint_installation_token

    config = get_connection_config(org_id, "github", workspace_id) or {}
    installation_id = config.get("installation_id")
    if not installation_id:
        msg = (
            "This GitHub connection has no installation id recorded, so no "
            "repository token can be issued. Reconnect GitHub to fix this."
        )
        mark_needs_reauth(org_id, "github", workspace_id, msg)
        raise OAuthReauthRequiredError(msg)

    cache_key = (org_id, workspace_id, str(installation_id))
    cached = _INSTALLATION_TOKEN_CACHE.get(cache_key)
    now = datetime.now(timezone.utc)
    if cached and cached[1] - now > _REFRESH_SAFETY_MARGIN:
        return cached[0]

    try:
        minted = mint_installation_token(str(installation_id))
    except Exception as exc:  # noqa: BLE001 - mint failure is terminal for this install
        # No App, bad PEM, or GitHub 401/404: reconnect, not a retry.
        from ..core.exceptions import ConfigurationError, OAuthError

        if isinstance(exc, (OAuthError, ConfigurationError, OAuthReauthRequiredError)) or looks_like_auth_failure(
            exc
        ):
            wrapped = (
                exc
                if isinstance(exc, OAuthReauthRequiredError)
                else OAuthReauthRequiredError(
                    "GitHub installation access failed; reconnect GitHub to continue.",
                    cause=exc,
                )
            )
            mark_needs_reauth(org_id, "github", workspace_id, str(wrapped))
            raise wrapped from exc
        raise
    # No expiry reported (shouldn't happen — GitHub always sends one) means we
    # can't reason about validity, so don't cache it rather than cache it wrongly.
    if minted.expires_at is not None:
        expires_at = minted.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        _INSTALLATION_TOKEN_CACHE[cache_key] = (minted.token, expires_at)
    return minted.token


def looks_like_auth_failure(exc: BaseException) -> bool:
    """True when ``exc`` (or its cause chain) means the credential is dead.

    Conservative: a Drive 403 on one file must not force reconnect. Only 401,
    Notion ``unauthorized`` and invalid_grant/invalid_token/revoked count.
    """
    cur: BaseException | None = exc
    for _ in range(8):
        if cur is None:
            break
        if isinstance(cur, ConfigurationError):
            # This host's (or company app's) OAuth client was refused: its 401
            # says nothing about the person's token, so the connection stays.
            return False
        status = getattr(cur, "status_code", None)
        if status == 401:
            return True
        code = getattr(cur, "code", None)
        if isinstance(code, str) and code.lower() in {
            "unauthorized",
            "invalid_grant",
            "invalid_token",
        }:
            return True
        if code == 401:
            return True
        msg = str(cur).lower()
        needles = (
            "unauthorized",
            "invalid_grant",
            "invalid_token",
            "token has been revoked",
            "token revoked",
            "authentication failed",
            "must reconnect",
            "oauth_reauth",
        )
        if any(n in msg for n in needles):
            return True
        nxt = getattr(cur, "cause", None)
        if nxt is None:
            nxt = cur.__cause__
        cur = nxt if isinstance(nxt, BaseException) else None
    return False


def sanitize_reauth_reason(provider: str, reason: str | None) -> str | None:
    """Format raw API / OAuth errors into clean, human-friendly messages."""
    if not reason:
        return None
    raw = reason.strip()
    if not raw:
        return None
    low = raw.lower()
    provider_names = {
        "linear": "Linear",
        "google": "Google Drive",
        "drive": "Google Drive",
        "notion": "Notion",
        "slack": "Slack",
        "github": "GitHub",
    }
    name = provider_names.get(provider.lower(), provider.title())

    if any(
        k in low
        for k in (
            "401",
            "unauthorized",
            "invalid_grant",
            "invalid_token",
            "token revoked",
            "access revoked",
            "token has expired",
            "expired",
            "authentication failed",
            "forbidden",
        )
    ):
        return f"{name} access token expired or was revoked. Reconnect your account to resume syncing."

    import re
    cleaned = re.sub(r"https?://\S+", "", raw)
    cleaned = re.sub(r"For more information check:?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"Client error '401 Unauthorized' for url.*", "", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split()).strip(" :-,.")
    if not cleaned or len(cleaned) < 4:
        return f"{name} access expired. Reconnect to resume syncing."
    if len(cleaned) > 250:
        cleaned = cleaned[:247] + "..."
    return cleaned


def mark_needs_reauth(
    org_id: str,
    provider: str,
    workspace_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Sticky flag so Sources can show Reconnect without re-probing."""
    detail = sanitize_reauth_reason(provider, reason)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE oauth_connections
            SET needs_reauth = true, reauth_reason = %s
            WHERE org_id = %s AND provider = %s
              AND workspace_id IS NOT DISTINCT FROM %s
            """,
            (detail, org_id, provider, workspace_id),
        )


def clear_needs_reauth(
    org_id: str, provider: str, workspace_id: str | None = None
) -> None:
    """Clear after reconnect or a successful live provider call."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE oauth_connections
            SET needs_reauth = false, reauth_reason = NULL
            WHERE org_id = %s AND provider = %s
              AND workspace_id IS NOT DISTINCT FROM %s
            """,
            (org_id, provider, workspace_id),
        )


def list_connections(
    org_id: str, workspace_id: str | None = None
) -> list[OAuthConnectionInfo]:
    """List connections (metadata only, never the token); ``workspace_id`` picks a space's."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id::text, provider, external_workspace_id, "
            "external_workspace_name, created_at, source_config, "
            "needs_reauth, reauth_reason, last_sync_at "
            "FROM oauth_connections "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            # The org's own LLM credential lives in this table but is not a source;
            # every caller assumes rows are sources, so it is filtered here once.
            "AND provider <> 'llm' "
            "ORDER BY created_at DESC",
            (org_id, workspace_id),
        ).fetchall()
    return [
        OAuthConnectionInfo(
            id=r[0],
            provider=r[1],
            external_workspace_id=r[2],
            external_workspace_name=r[3],
            created_at=r[4],
            source_config=r[5],
            needs_reauth=bool(r[6]),
            reauth_reason=r[7],
            last_sync_at=r[8],
        )
        for r in rows
    ]


def set_connection_config(
    org_id: str, provider: str, config: dict, workspace_id: str | None = None
) -> None:
    """Store this connection's provider-specific scope config (JSONB).

    Raises ``ConfigurationError`` when the connection does not exist.
    """
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE oauth_connections SET source_config = %s "
            "WHERE org_id = %s AND provider = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "RETURNING id",
            (Json(config), org_id, provider, workspace_id),
        ).fetchone()
    if not row:
        raise ConfigurationError(
            f"No {provider!r} connection for this organization. Connect it first."
        )


def get_connection_config(
    org_id: str, provider: str, workspace_id: str | None = None
) -> dict | None:
    """The stored scope config, or ``None`` (not connected, or never configured)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT source_config FROM oauth_connections "
            "WHERE org_id = %s AND provider = %s AND workspace_id IS NOT DISTINCT FROM %s",
            (org_id, provider, workspace_id),
        ).fetchone()
    if not row:
        return None
    return row[0]


def connected_anywhere(org_id: str, provider: str) -> list[tuple[str | None, dict]]:
    """Every ``provider`` connection in this org as ``(external_workspace_id, source_config)``.

    Company-wide first. Act's account checks read it: a tool may be connected in only one space.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT external_workspace_id, source_config FROM oauth_connections "
            "WHERE org_id = %s AND provider = %s ORDER BY workspace_id NULLS FIRST",
            (org_id, provider),
        ).fetchall()
    return [(r[0], r[1] or {}) for r in rows]


def clear_installation_token_cache(
    org_id: str, workspace_id: str | None = None
) -> None:
    """Drop cached GitHub installation tokens for this tenant scope."""
    doomed = [k for k in _INSTALLATION_TOKEN_CACHE if k[0] == org_id and k[1] == workspace_id]
    for key in doomed:
        _INSTALLATION_TOKEN_CACHE.pop(key, None)


def delete_connection(
    org_id: str, connection_id: str, *, workspace_id: str | None = None
) -> str:
    """Delete one connection in this org (and workspace); return its provider.

    Raises ``ConfigurationError`` when the id is missing or out of scope.
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            DELETE FROM oauth_connections
            WHERE id = %s::uuid
              AND org_id = %s::uuid
              AND workspace_id IS NOT DISTINCT FROM %s::uuid
            RETURNING provider
            """,
            (connection_id, org_id, workspace_id),
        ).fetchone()
    if not row:
        raise ConfigurationError("No such connection for this organization.")
    provider = row[0]
    if provider == "github":
        clear_installation_token_cache(org_id, workspace_id)
    return provider

