"""Shared connection lifecycle helpers for admin + workspace routes.

Keeps Disconnect / folder-swap purge and OAuth-reauth HTTP mapping in one
place so org Sources and Spaces never drift.
"""

from __future__ import annotations

from fastapi import HTTPException

from ..auth import delete_connection, get_connection_config
from ..core.exceptions import ConfigurationError, OAuthReauthRequiredError, SourceError
from ..vectorstore import build_vector_store

# Providers that store documents/chunks. GitHub is live-only — disconnect
# only drops the oauth_connections row.
_INDEXED_PROVIDERS = frozenset({"notion", "google"})


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


def raise_token_http(exc: Exception) -> None:
    """Map credential failures to actionable HTTP errors (never bare 500).

    ``OAuthReauthRequiredError`` → 401 with a structured detail the frontend
    uses for a Reconnect CTA. Other config/source errors → 400.
    """
    if isinstance(exc, OAuthReauthRequiredError):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "oauth_reauth_required",
                "message": str(exc),
            },
        ) from exc
    if isinstance(exc, (ConfigurationError, SourceError)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc
