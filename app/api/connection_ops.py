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
from ..rag.query_cache import delete_org_entries
from ..sources.factory import INDEXED_PROVIDERS
from ..vectorstore import build_vector_store

logger = logging.getLogger(__name__)

# Providers that store documents/chunks. GitHub is live-only — disconnect only
# drops the oauth_connections row. Taken from the source factory rather than
# listed again here: this set was hand-kept and had gone stale, omitting Linear,
# so "Indexed documents for this source will be deleted" was false for it and a
# disconnected Linear kept answering questions.
_INDEXED_PROVIDERS = frozenset(INDEXED_PROVIDERS)


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
    # The same reason an ingest clears it: a cached answer outlives the content
    # it was built from, so for up to the TTL a disconnected source keeps
    # answering. Org-wide because the provider is folded into the question hash.
    try:
        delete_org_entries(org_id)
    except Exception:  # noqa: BLE001 - a stale cache entry, never a failed disconnect
        logger.warning("Could not clear the answer cache after a disconnect", exc_info=True)
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


# -- Google Forms: the surveys an admin has opted into ----------------------
#
# Forms are the one source where reading is NOT implied by connecting. A Drive
# token can see every form in the account, and `sentiment.py` classified all of
# them -- so switching the feature on would have read an admin's unrelated
# personal surveys and turned them into charts nobody asked for. The stored
# list is therefore an ALLOW-LIST, and no list means no reading (see
# `record_form_sentiment`), not "all of them".
#
# Stored under `source_config.form_ids` alongside Drive's folder_id, so it
# lives on the connection whose scope it belongs to -- org-wide Sources or one
# space -- and inherits the same org-delete cascade.

#: A generous cap on the allow-list. It exists because the whole config is one
#: JSONB column shared with Drive's folder, not because 25 surveys is a limit
#: anyone will reach.
MAX_FORM_IDS = 25


def list_google_forms(org_id: str, *, workspace_id: str | None = None) -> list[dict]:
    """Forms this connection's token can see, for the picker.

    Listed through DRIVE, which `drive.readonly` already covers -- the Forms
    API has no listing endpoint. So the picker works even before the tenant
    reconnects for the responses scope, which matters: picking the surveys is
    step one, and being told to reconnect is step two.
    """
    from ..auth.credentials import get_live_connection_token
    from ..sources.google_forms import GoogleFormsReader

    token = get_live_connection_token(org_id, "google", workspace_id)
    return [
        {"id": form.form_id, "title": form.title}
        for form in GoogleFormsReader(token).list_forms()
    ]


def set_google_form_ids(
    org_id: str,
    form_ids: list[str],
    *,
    workspace_id: str | None = None,
) -> dict:
    """Store the allow-list, MERGED into the existing config. Returns it.

    Merged rather than replaced because `set_connection_config` overwrites the
    whole JSONB column and Drive's `folder_id` lives in it: writing a bare
    ``{"form_ids": [...]}`` would silently un-scope Drive ingestion, which
    would then read the entire account.

    Ids are validated against what the token can actually see. An id we cannot
    see would sit in the config forever, failing every classification run with
    a 404 that reads as a broken feature rather than a stale selection.
    """
    from ..auth.credentials import set_connection_config

    if not isinstance(form_ids, list) or not all(isinstance(f, str) for f in form_ids):
        raise ConfigurationError("form_ids must be a list of form id strings")
    if len(form_ids) > MAX_FORM_IDS:
        raise ConfigurationError(
            f"At most {MAX_FORM_IDS} forms can be selected at once."
        )

    wanted = [f.strip() for f in form_ids if f.strip()]
    if wanted:
        visible = {f["id"]: f for f in list_google_forms(org_id, workspace_id=workspace_id)}
        unknown = [f for f in wanted if f not in visible]
        if unknown:
            raise ConfigurationError(
                "These forms are not visible to this Google connection: "
                + ", ".join(unknown)
            )
        titles = {f: visible[f]["title"] for f in wanted}
    else:
        titles = {}

    existing = get_connection_config(org_id, "google", workspace_id=workspace_id) or {}
    config = {
        **existing,
        "form_ids": wanted,
        # Snapshotted like Slack's channel_names, for the same reason: the UI
        # names the selection without a round trip to Google, and a renamed
        # form keeps a name we once knew rather than showing a bare id.
        "form_titles": titles,
    }
    set_connection_config(org_id, "google", config, workspace_id=workspace_id)
    return config
