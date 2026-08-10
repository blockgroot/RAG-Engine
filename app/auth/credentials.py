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

from psycopg.types.json import Json

from ..core.exceptions import ConfigurationError, OAuthReauthRequiredError
from ..db.connection import get_connection
from ..security import decrypt, encrypt
from .base import OAuthTokens

# How far ahead of actual expiry we proactively refresh. Google access tokens
# live ~1h; refreshing 5 minutes early absorbs request latency/clock skew
# without adding a retry loop.
_REFRESH_SAFETY_MARGIN = timedelta(minutes=5)

# In-process cache of minted GitHub installation tokens, keyed by
# ``(org_id, workspace_id, installation_id)``. GitHub installation tokens are
# valid for an hour, so minting one per question would burn rate limit and add a
# round-trip for no benefit. Deliberately process-local (not Postgres): it holds
# live credentials, it is cheap to rebuild after a restart, and a shared cache
# would need its own encryption + invalidation story for zero gain.
#
# The key includes ``org_id`` so a cache hit can never hand one tenant another
# tenant's token — the same "scope is part of the key, never assumed" discipline
# every query in this module follows.
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


def save_connection(
    org_id: str,
    provider: str,
    tokens: OAuthTokens,
    *,
    connected_by_user_id: str | None = None,
    workspace_id: str | None = None,
) -> str:
    """Encrypt and upsert a connection for ``provider``.

    One row per ``(org_id, provider)`` when ``workspace_id`` is ``None``
    (today's org-wide admin connection, unchanged) — a second connect for the
    same provider replaces the first (e.g. reconnecting after revoking
    access). One row per ``(org_id, provider, workspace_id)`` when
    ``workspace_id`` is set (Workspace-within-a-Workspace): an employee's
    personal connection for their sub-workspace, kept fully separate from the
    org-wide connection and from any other workspace's connection for the
    same provider (see the two partial unique indexes in schema.sql). Which
    ``ON CONFLICT`` target applies depends on whether ``workspace_id`` is
    ``None`` — Postgres requires the inference clause's predicate to match
    the partial index's predicate exactly, so this can't be one static query.
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
    """Return the decrypted access token for this ``provider`` connection.

    Always scoped by BOTH ``org_id`` and ``provider`` — never provider alone —
    so this can never return another org's token. ``workspace_id`` (default
    ``None``) additionally scopes to the org-wide connection vs. a specific
    sub-workspace's personal connection. Raises ``ConfigurationError`` if no
    connection exists (the admin/employee hasn't connected this provider yet).
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

    # GitHub is the one provider where the token callers need is NOT the token
    # we stored: the stored value is the *user* access token (proof of who
    # connected), while reading repositories requires a short-lived
    # *installation* token minted from the App's private key. Handled here, in
    # front of the generic refresh logic, so no caller has to know that —
    # exactly why this function exists (see the docstring's D10 note).
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
    except OAuthReauthRequiredError as exc:
        mark_needs_reauth(org_id, provider, workspace_id, str(exc))
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
    """Return a valid GitHub installation access token for this connection.

    Reads ``installation_id`` from the connection's ``source_config`` (written by
    the connect callback after verifying it against the authorizing user — see
    ``github_oauth.py``), mints a token from the App private key, and caches it
    until shortly before it expires.

    Note what is deliberately NOT done: the stored user access token is never
    returned as a fallback. If minting can't happen, the caller gets an error, not
    a credential that would fail confusingly at the first repo read.
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
        # Missing/uninstalled App, bad PEM, or GitHub 401/404 on the install —
        # all need reconnect, not a soft retry. ConfigurationError (no install id)
        # is raised above; OAuthError comes from the mint HTTP call.
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
    """True when ``exc`` (or its cause chain) is credential death, not a blip.

    Conservative on purpose: a Drive 403 on one file must not force reconnect.
    We treat HTTP 401, Notion ``unauthorized``, and explicit invalid_grant /
    invalid_token / revoked language as terminal auth failures.
    """
    cur: BaseException | None = exc
    for _ in range(8):
        if cur is None:
            break
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


def mark_needs_reauth(
    org_id: str,
    provider: str,
    workspace_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Sticky flag so Sources can show Reconnect without re-probing."""
    detail = (reason or "").strip() or None
    if detail and len(detail) > 500:
        detail = detail[:497] + "..."
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
    """List connections (metadata only — never the decrypted token).

    ``workspace_id`` (default ``None``) lists the org-wide connections;
    passing a workspace's id lists only that sub-workspace's personal
    connections instead.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id::text, provider, external_workspace_id, "
            "external_workspace_name, created_at, source_config, "
            "needs_reauth, reauth_reason "
            "FROM oauth_connections "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
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
        )
        for r in rows
    ]


def set_connection_config(
    org_id: str, provider: str, config: dict, workspace_id: str | None = None
) -> None:
    """Store this org's provider-specific ingestion scope config.

    Generic on purpose (Google Integration Phase 4): Notion never needs this
    (an integration token already only sees pages explicitly shared with it),
    but Google Drive requires the admin to designate an in-scope folder up
    front, and a future GitHub/Slack adapter will need its own shape (a repo
    name, a channel list). Rather than one column per provider, this stores
    whatever dict that provider's admin flow collected as JSONB.

    Requires the ``(org_id, provider)`` connection row to already exist (i.e.
    the OAuth connect flow has run) — raises ``ConfigurationError`` otherwise,
    mirroring ``get_connection_token``'s not-connected error, since scope
    config with no underlying connection is meaningless.
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
    """Return the stored provider-specific scope config, or ``None``.

    ``None`` covers both "never connected" and "connected but never
    configured" — callers that need to distinguish those cases should check
    ``list_connections``/``get_connection_token`` separately; this is purely
    about the optional scope config.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT source_config FROM oauth_connections "
            "WHERE org_id = %s AND provider = %s AND workspace_id IS NOT DISTINCT FROM %s",
            (org_id, provider, workspace_id),
        ).fetchone()
    if not row:
        return None
    return row[0]


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
    """Delete one OAuth connection row owned by this org (and optional workspace).

    Returns the provider name so callers can purge indexed docs for Notion/Drive.
    Raises ``ConfigurationError`` when the id is missing or not in scope —
    never deletes across a tenant or workspace boundary.
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

