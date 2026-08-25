"""Shared connection lifecycle helpers for admin + workspace routes.

Keeps Disconnect / folder-swap purge and OAuth-reauth HTTP mapping in one
place so org Sources and Spaces never drift.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from ..auth import delete_connection, get_connection_config
from ..auth.credentials import (
    clear_needs_reauth,
    looks_like_auth_failure,
    mark_needs_reauth,
)
from ..core.exceptions import ConfigurationError, OAuthReauthRequiredError, SourceError
from ..db.connection import get_connection
from ..vectorstore import build_vector_store

logger = logging.getLogger(__name__)

# Providers that store documents/chunks. GitHub is live-only — disconnect
# only drops the oauth_connections row.
_INDEXED_PROVIDERS = frozenset({"notion", "google", "slack"})


def purge_provider_documents(
    org_id: str, provider: str, *, workspace_id: str | None = None
) -> int:
    """Delete every indexed doc for ``provider`` in this org/workspace scope."""
    if provider not in _INDEXED_PROVIDERS:
        return 0
    store = build_vector_store()
    return store.delete_all_source_documents(
        org_id, provider, workspace_id=workspace_id
    )


def disconnect_connection(
    org_id: str,
    connection_id: str,
    *,
    workspace_id: str | None = None,
) -> dict:
    """Delete the OAuth row and purge indexed docs for Notion/Drive.

    Returns ``{provider, documents_purged}``. Raises ``ConfigurationError`` when
    the connection is missing or out of scope.
    """
    provider = delete_connection(org_id, connection_id, workspace_id=workspace_id)
    purged = purge_provider_documents(org_id, provider, workspace_id=workspace_id)
    return {"provider": provider, "documents_purged": purged}


def folder_id_changed(
    org_id: str,
    provider: str,
    new_folder_id: str,
    *,
    workspace_id: str | None = None,
) -> bool:
    """True when a Drive folder PUT replaces a different previously-saved id."""
    existing = get_connection_config(org_id, provider, workspace_id=workspace_id) or {}
    old = existing.get("folder_id")
    return bool(old) and old != new_folder_id


def slack_channels_changed(
    org_id: str,
    provider: str,
    new_channel_ids: list[str],
    *,
    workspace_id: str | None = None,
) -> bool:
    """True when a Slack channel-picker PUT drops a previously-saved channel.

    Same purpose as ``folder_id_changed``: a dropped channel's already-ingested
    threads must not keep being cited once the admin de-selects it. Adding a
    NEW channel to an existing selection is not a "change" in this sense — the
    old channels' content is still valid, so no purge is needed for a
    pure-addition PUT.
    """
    existing = get_connection_config(org_id, provider, workspace_id=workspace_id) or {}
    old_ids = set(existing.get("channel_ids") or [])
    return bool(old_ids) and not old_ids.issubset(set(new_channel_ids))


def refresh_slack_channel_names(
    org_id: str,
    *,
    workspace_id: str | None = None,
) -> list[tuple[str, str]]:
    """API-facing wrapper — the implementation lives with the Slack helpers.

    Kept as a name here because the change-check routes read better calling
    it, but the same refresh runs from the ingest worker and the scheduler, so
    it cannot live in the API layer.
    """
    from ..sources.slack_utils import refresh_channel_names

    return refresh_channel_names(org_id, workspace_id=workspace_id)


def find_slack_channel_conflict(
    org_id: str,
    channel_ids: list[str],
    *,
    exclude_workspace_id: str | None,
) -> str | None:
    """Return the id of another connection (org-wide or a sibling workspace)
    already claiming one of ``channel_ids``, or ``None`` if there's no clash.

    Decision D10: a channel is registered under exactly one ``workspace_id``
    at a time. ``validate_slack_channels`` only confirms Slack-side
    visibility — nothing there prevents the SAME channel being saved into two
    different connections for this org, which would double-embed it and make
    "which scope answered this" ambiguous. ``exclude_workspace_id`` is the
    connection currently being saved (``None`` for the org-wide PUT), so
    re-saving the same channel set to the same connection is never flagged
    as a conflict with itself.
    """
    wanted = set(channel_ids)
    if not wanted:
        return None
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id::text, workspace_id::text, source_config "
            "FROM oauth_connections "
            "WHERE org_id = %s AND provider = 'slack' "
            "AND NOT (workspace_id IS NOT DISTINCT FROM %s)",
            (org_id, exclude_workspace_id),
        ).fetchall()
    for connection_id, _workspace_id, source_config in rows:
        claimed = set((source_config or {}).get("channel_ids") or [])
        if wanted & claimed:
            return connection_id
    return None


def raise_token_http(
    exc: Exception,
    *,
    org_id: str | None = None,
    provider: str | None = None,
    workspace_id: str | None = None,
) -> None:
    """Map credential failures to actionable HTTP errors (never bare 500).

    ``OAuthReauthRequiredError`` and auth-shaped ``SourceError`` (Notion 401,
    invalid_grant, …) → 401 with ``oauth_reauth_required`` so the frontend can
    show Reconnect. Also sticks ``needs_reauth`` on the connection row when
    org/provider are known, so the flag survives a page reload.
    Other config/source errors → 400.
    """
    auth_exc: Exception = exc
    if isinstance(exc, SourceError) and looks_like_auth_failure(exc):
        auth_exc = OAuthReauthRequiredError(
            "This connection's access has expired or been revoked; reconnect "
            "it to continue.",
            cause=exc,
        )
    if isinstance(auth_exc, OAuthReauthRequiredError):
        if org_id and provider:
            mark_needs_reauth(org_id, provider, workspace_id, str(auth_exc))
        raise HTTPException(
            status_code=401,
            detail={
                "code": "oauth_reauth_required",
                "message": str(auth_exc),
            },
        ) from exc
    if isinstance(exc, (ConfigurationError, SourceError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


def note_live_success(
    org_id: str, provider: str, *, workspace_id: str | None = None
) -> None:
    """Clear sticky reauth after a successful live provider call."""
    clear_needs_reauth(org_id, provider, workspace_id=workspace_id)
