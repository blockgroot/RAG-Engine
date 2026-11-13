"""Connection lifecycle helpers shared by admin and space routes, so they never drift."""

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
from ..sources.factory import INDEXED_PROVIDERS
from ..sources.google_drive_utils import preflight_folder_sharing
from ..vectorstore import build_vector_store

logger = logging.getLogger(__name__)

# Providers that store documents (GitHub is live-only). From the factory: a
# hand-kept list omitted Linear, which kept answering after disconnect.
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
    """Delete the connection and purge its indexed documents.

    Returns ``{provider, documents_purged}``; ``ConfigurationError`` if missing or out of scope.
    """
    provider = delete_connection(org_id, connection_id, workspace_id=workspace_id)
    purged = purge_provider_documents(org_id, provider, workspace_id=workspace_id)
    # Graph entities still NAME things from this source; purge them too.
    try:
        from ..graph.builder import purge_provider

        purge_provider(org_id, workspace_id, provider)
    except Exception:  # noqa: BLE001 - logged; the documents are already gone
        logger.warning("Could not purge graph rows after a disconnect", exc_info=True)
    return {"provider": provider, "documents_purged": purged}


def drive_sharing_report(token: str, folder_id: str) -> dict | None:
    """A warning if this folder has files whose sharing we cannot read.

    Returned by both folder-save routes so whoever picks the folder sees it
    then. ``None`` when all is readable, the folder is empty, or the check
    failed: a warning on a healthy folder teaches people to dismiss it.
    """
    report = preflight_folder_sharing(token, folder_id)
    if report["failed"] or not report["unreadable"]:
        return None

    n, checked = report["unreadable"], report["checked"]
    files = "file" if n == 1 else "files"
    scope = f"{n} of the {checked} files" if n < checked else (
        "The file" if n == 1 else f"All {n} files"
    )
    more = " We only checked the top of this folder, so there may be more." if report["truncated"] else ""
    return {
        "kind": "permissions",
        "count": n,
        "checked": checked,
        "files": report["files"],
        "truncated": report["truncated"],
        "title": f"Only you will see {n} {files} in this folder",
        # Consequence first, no jargon. "Only you": such a file is indexed for the
        # connected account alone (`DocAccess.owner_only`), not left out.
        "detail": (
            f"{scope} we looked at don\u2019t tell us who they\u2019re shared with, so "
            "Handbook adds them for the connected Google account only. Anyone "
            "else in this space will be told the document isn\u2019t shared with "
            f"them.{more}"
        ),
        "fix": (
            "Google only shares that with an owner or editor. Ask the file\u2019s owner "
            "to make the connected Google account an editor of the folder, or turn on "
            "\u201cViewers and commenters can see who else has access\u201d in the sharing "
            "settings. Then save the folder again."
        ),
    }


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
    """True when a Slack channel-picker PUT drops a saved channel (its threads are purged).

    Adding a channel is not a change.
    """
    existing = get_connection_config(org_id, provider, workspace_id=workspace_id) or {}
    old_ids = set(existing.get("channel_ids") or [])
    return bool(old_ids) and not old_ids.issubset(set(new_channel_ids))


def refresh_slack_channel_names(
    org_id: str,
    *,
    workspace_id: str | None = None,
) -> list[tuple[str, str]]:
    """API name for the Slack refresh; it lives with the Slack helpers (worker and scheduler use it)."""
    from ..sources.slack_utils import refresh_channel_names

    return refresh_channel_names(org_id, workspace_id=workspace_id)


def find_slack_channel_conflict(
    org_id: str,
    channel_ids: list[str],
    *,
    exclude_workspace_id: str | None,
) -> str | None:
    """Id of another connection already claiming one of ``channel_ids``, or ``None``.

    A channel belongs to one connection per org, else it is embedded twice and
    the answering scope is ambiguous. ``exclude_workspace_id`` is the one being
    saved, so re-saving is not a clash.
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
    """Map credential failures to actionable HTTP errors, never a bare 500.

    Reauth-shaped failures are 401 ``oauth_reauth_required`` (the UI shows
    Reconnect) and set ``needs_reauth`` so it survives reload; others are 400.
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


# -- Google Forms: the surveys an admin opted into ---------------------------
#
# `source_config.form_ids` is an ALLOW-LIST: a Drive token sees every form in
# the account, so no list means nothing is read.

#: Cap on the allow-list (the config is one JSONB column shared with Drive).
MAX_FORM_IDS = 25


def list_google_forms(org_id: str, *, workspace_id: str | None = None) -> list[dict]:
    """Forms this token can see, for the picker.

    Listed through Drive (the Forms API cannot list), so it works before a
    reconnect for the responses scope.
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
    """Store the allow-list merged into the existing config; return it.

    Merged: replacing would drop Drive's `folder_id` and un-scope ingestion.
    Ids are checked against what the token can see, so no stale id 404s forever.
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
        # Names snapshotted like Slack's channel_names: no round trip, and a renamed form keeps a name.
        "form_titles": titles,
    }
    set_connection_config(org_id, "google", config, workspace_id=workspace_id)
    return config


# -- Linear: the teams a connection reads -----------------------------------
# Sign-in grants every team, so teams are picked here; nothing is indexed until then.


def list_linear_teams(org_id: str, *, workspace_id: str | None = None) -> list[dict]:
    """Teams this connection's token can see, for the picker."""
    from ..auth.credentials import get_live_connection_token
    from ..sources.linear import LinearAdapter

    token = get_live_connection_token(org_id, "linear", workspace_id)
    return LinearAdapter(token=token, oauth=True).list_teams()


def set_linear_teams(
    org_id: str, connection_id: str, team_ids, *, workspace_id: str | None = None,
) -> dict:
    """Save the picked teams and queue the sync; dropped teams' issues are purged first.

    A first pick purges too: older connections indexed every team, and the
    sync's mass-removal guard would keep them.
    """
    from ..auth.credentials import get_live_connection_token, set_connection_config
    from ..jobs.autosync import sync_now
    from ..sources.linear import validate_linear_teams

    if not isinstance(team_ids, list) or not all(isinstance(t, str) for t in team_ids):
        raise HTTPException(status_code=400, detail="team_ids must be a list of team id strings")
    try:
        token = get_live_connection_token(org_id, "linear", workspace_id)
        config = validate_linear_teams(token, team_ids)
        old = set((get_connection_config(org_id, "linear", workspace_id=workspace_id) or {})
                  .get("team_ids") or [])
        dropped = not old or not old.issubset(config["team_ids"])
        set_connection_config(org_id, "linear", config, workspace_id=workspace_id)
        note_live_success(org_id, "linear", workspace_id=workspace_id)
    except (ConfigurationError, SourceError, OAuthReauthRequiredError) as exc:
        raise_token_http(exc, org_id=org_id, provider="linear", workspace_id=workspace_id)
    purged = purge_provider_documents(org_id, "linear", workspace_id=workspace_id) if dropped else 0
    sync_now(org_id, connection_id, provider="linear", workspace_id=workspace_id)
    return {
        "connection_id": connection_id,
        "provider": "linear",
        "config": config,
        "teams_changed": dropped,
        "documents_purged": purged,
    }
