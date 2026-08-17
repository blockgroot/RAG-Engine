"""Workspaces router: sub-workspace CRUD, membership, connections, ingest.

Every route requires an authenticated session (``get_session`` — any org
member, not just admins, since creating a personal sub-workspace is an
employee capability, not an admin-only one per CLAUDE.md's Workspace-within-
a-Workspace plan). Every route that operates on an EXISTING workspace resolves
the ``workspace_id`` from the URL path and immediately calls
``assert_member`` (via ``get_workspace_role``/``require_workspace_owner`` in
``deps.py``) before touching anything else — mirroring exactly how
``deps.require_admin`` is the one gate every admin route trusts. A workspace's
own ``org_id`` is always checked against the caller's session ``org_id``
inside that gate, so a stale/forged workspace_id from a different org fails
closed instead of ever resolving (see app/workspaces/store.py::assert_member).
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..auth import (
    create_magic_link_token,
    get_connection_config,
    get_live_connection_token,
    get_user,
    list_connections,
    list_members,
    send_workspace_invite_email_safe,
    set_connection_config,
)
from ..config.settings import ApiSettings
from ..core.exceptions import (
    AuthError,
    ConfigurationError,
    NotFoundError,
    OAuthReauthRequiredError,
    SourceError,
)
from ..githublive import refresh_installation_scope
from ..ingestion import detect_source_changes
from ..jobs import JobAlreadyActiveError, enqueue, get_job, has_active_job, list_jobs
from ..sources import (
    build_source_adapter,
    extract_drive_folder_id,
    join_public_channels,
    list_channel_members,
    list_slack_channels,
    search_drive_folders,
    validate_drive_folder,
    validate_slack_channels,
)
from ..db.connection import get_connection
from ..workspaces import (
    create_workspace,
    delete_workspace,
    invite_member,
    list_my_workspaces,
    list_workspace_members,
    make_workspace_owner,
    remove_workspace_member as store_remove_workspace_member,
)
from .connection_ops import (
    disconnect_connection,
    find_slack_channel_conflict,
    folder_id_changed,
    note_live_success,
    purge_provider_documents,
    raise_token_http,
    slack_channels_changed,
)
from .deps import SessionClaims, get_session, get_workspace_role, require_workspace_owner
from .serialize import job_payload
from .setup_status import content_setup_status
from .validation import MAX_EMAIL_CHARS, MAX_NAME_CHARS, MAX_URL_CHARS, bounded

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("")
def create(body: dict, session: SessionClaims = Depends(get_session)):
    name = bounded(
        (body.get("name") or "").strip(), field="Workspace name", limit=MAX_NAME_CHARS
    )
    workspace_id = create_workspace(session.org_id, name, session.user_id)
    return {"id": workspace_id, "name": name, "role": "owner"}


@router.get("")
def list_mine(session: SessionClaims = Depends(get_session)):
    return [
        {"id": w.id, "name": w.name, "role": w.role, "created_by": w.created_by}
        for w in list_my_workspaces(session.org_id, session.user_id)
    ]


@router.get("/{workspace_id}")
def get_workspace(
    workspace_id: str,
    session: SessionClaims = Depends(get_session),
    role: str = Depends(get_workspace_role),
):
    """Workspace identity + the same ready_to_ask gate shape as ``GET /me``.

    Scoped only to this workspace's connections/docs/jobs — never org-wide
    rows — so Ask unlocks after *this* workspace's sync succeeds.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT name, created_by::text FROM workspaces "
            "WHERE id = %s AND org_id = %s",
            (workspace_id, session.org_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    status = content_setup_status(session.org_id, workspace_id=workspace_id)
    return {
        "id": workspace_id,
        "name": row[0],
        "role": role,
        "created_by": row[1],
        # Whether THIS workspace has its own GitHub connection, so the workspace
        # chat can offer a Code tab. Scoped to the workspace on purpose: an
        # org-wide GitHub connection must NOT light this up, or a member would be
        # offered a Code tab that then answers from a scope they aren't in.
        "github_connected": _workspace_github_connected(session.org_id, workspace_id),
        # Same rule as /me: keyed on ingested Slack documents for THIS
        # workspace, so a Slack tab only appears once it can actually answer.
        "slack_ready": _workspace_slack_ready(session.org_id, workspace_id),
        **status,
    }



@router.delete("/{workspace_id}")
def delete_space(
    workspace_id: str,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    """Owner-only: permanently delete this space and its scoped content.

    Org-wide policies and org GitHub are untouched. Workspace docs, connections,
    conversations, and membership cascade away with the ``workspaces`` row.
    """
    try:
        delete_workspace(workspace_id, session.org_id, session.user_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"status": "deleted", "workspace_id": workspace_id}



def _workspace_github_connected(org_id: str, workspace_id: str) -> bool:
    """True only when this workspace has its OWN github connection row."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM oauth_connections "
            "WHERE org_id = %s AND provider = 'github' AND workspace_id = %s",
            (org_id, workspace_id),
        ).fetchone()
    return row is not None


def _workspace_slack_ready(org_id: str, workspace_id: str) -> bool:
    """True when this workspace has its OWN ingested Slack threads.

    Scoped to the workspace (never ``IS NULL``/org-wide) for the same reason
    ``_workspace_github_connected`` is: an org-wide Slack corpus must not light
    up a workspace's Slack tab, which retrieves only this workspace's chunks
    and would therefore answer nothing.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM documents "
            "WHERE org_id = %s AND source_provider = 'slack' AND workspace_id = %s "
            "LIMIT 1",
            (org_id, workspace_id),
        ).fetchone()
    return row is not None


def _reject_if_github(provider: str, what: str) -> None:
    """GitHub has no ingestion — refuse sync-shaped operations on a workspace too.

    Same reasoning as the admin router's guard: without this, ``/ingest`` would
    enqueue a job the worker cannot run and the owner would watch it fail later
    with an obscure "Unknown source type".
    """
    if provider == "github":
        raise HTTPException(
            status_code=400,
            detail=(
                f"{what} does not apply to GitHub. Repositories are read live when "
                "a question is asked, so there is nothing to sync or configure "
                "here — the repositories in scope are chosen on GitHub."
            ),
        )


@router.get("/{workspace_id}/members")
def get_members(workspace_id: str, _role: str = Depends(get_workspace_role)):
    return list_workspace_members(workspace_id)


def _workspace_name(org_id: str, workspace_id: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT name FROM workspaces WHERE id = %s AND org_id = %s",
            (workspace_id, org_id),
        ).fetchone()
    return row[0] if row else None


def _notify_workspace_invite(
    workspace_id: str,
    session: SessionClaims,
    email: str,
    background_tasks: BackgroundTasks,
    settings: ApiSettings,
) -> None:
    """Email a sign-in shortcut for a workspace membership that ALREADY exists.

    Membership is granted by the caller (an insert into ``workspace_members``)
    before this ever runs — this only sends a courtesy notification, never a
    gate. If the token inside ``link`` expires unused, the person is not
    locked out: they sign in the ordinary way from the login page, and the
    workspace is already sitting in their list, independent of this email.
    """
    workspace_name = _workspace_name(session.org_id, workspace_id) or "your workspace"
    inviter = get_user(session.user_id)
    token = create_magic_link_token(email)
    base = (settings.frontend_url or "").rstrip("/")
    link = f"{base}/verify?token={token}"
    background_tasks.add_task(
        send_workspace_invite_email_safe,
        email,
        link,
        workspace_name=workspace_name,
        inviter_email=inviter.email if inviter else None,
    )


@router.post("/{workspace_id}/members")
def invite(
    workspace_id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
    settings: ApiSettings = Depends(ApiSettings.from_env),
):
    email = bounded(
        (body.get("email") or "").strip().lower(),
        field="Email",
        limit=MAX_EMAIL_CHARS,
    )
    if "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    try:
        invite_member(workspace_id, session.org_id, session.user_id, email)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _notify_workspace_invite(workspace_id, session, email, background_tasks, settings)
    return {"status": "invited", "email": email}


@router.post("/{workspace_id}/members/{user_id}/resend-invite")
def resend_invite(
    workspace_id: str,
    user_id: str,
    background_tasks: BackgroundTasks,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
    settings: ApiSettings = Depends(ApiSettings.from_env),
):
    """Re-send the workspace-invite notification email.

    Never re-creates or re-validates membership — that already happened when
    they were first added. Purely re-sends the same courtesy email with a
    fresh sign-in link, for a colleague who missed the first one or let its
    (short-lived) token age out before opening it.
    """
    match = next(
        (m for m in list_workspace_members(workspace_id) if m["user_id"] == user_id), None
    )
    if match is None:
        raise HTTPException(status_code=404, detail="Not a member of this workspace")
    _notify_workspace_invite(workspace_id, session, match["email"], background_tasks, settings)
    return {"status": "invited", "email": match["email"]}


@router.post("/{workspace_id}/members/{user_id}/make-owner")
def make_owner(
    workspace_id: str,
    user_id: str,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    """Promote a space member to owner (needed before removing a sole owner)."""
    try:
        make_workspace_owner(workspace_id, session.org_id, session.user_id, user_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "owner", "user_id": user_id}


@router.delete("/{workspace_id}/members/{user_id}")
def remove_workspace_member(
    workspace_id: str,
    user_id: str,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    """Owner-only: remove a member from this space (their org account is untouched)."""
    try:
        store_remove_workspace_member(workspace_id, session.org_id, session.user_id, user_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "removed", "user_id": user_id}


def _owned_workspace_connection(org_id: str, workspace_id: str, connection_id: str):
    """Return the connection if it belongs to this (org_id, workspace_id), else 404."""
    owned = {c.id: c for c in list_connections(org_id, workspace_id=workspace_id)}
    conn = owned.get(connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="No such connection for this workspace")
    return conn


@router.get("/{workspace_id}/connections")
def get_connections(workspace_id: str, session: SessionClaims = Depends(get_session), _role: str = Depends(get_workspace_role)):
    return [
        {
            "id": c.id,
            "provider": c.provider,
            "external_workspace_name": c.external_workspace_name,
            "created_at": c.created_at.isoformat(),
            "source_config": c.source_config,
            "needs_reauth": c.needs_reauth,
            "reauth_reason": c.reauth_reason,
        }
        for c in list_connections(session.org_id, workspace_id=workspace_id)
    ]


@router.get("/{workspace_id}/connections/{connection_id}/drive-folders")
def search_connection_drive_folders(
    workspace_id: str,
    connection_id: str,
    q: str = "",
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    """List Drive folders this connection's account can see (folder-picker dropdown).

    Same shape as the admin ``GET /admin/connections/{id}/drive-folders``
    route, resolved against this workspace's own connection.
    """
    conn = _owned_workspace_connection(session.org_id, workspace_id, connection_id)
    if conn.provider != "google":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Folder search is only supported for Google Drive "
                f"(this connection is {conn.provider!r})."
            ),
        )
    try:
        token = get_live_connection_token(session.org_id, conn.provider, workspace_id=workspace_id)
        folders = search_drive_folders(token, q)
    except (SourceError, ConfigurationError, OAuthReauthRequiredError) as exc:
        raise_token_http(
            exc,
            org_id=session.org_id,
            provider=conn.provider,
            workspace_id=workspace_id,
        )
    return {"folders": folders}


@router.get("/{workspace_id}/connections/{connection_id}/slack-channels")
def list_workspace_connection_slack_channels(
    workspace_id: str,
    connection_id: str,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    """List channels this workspace's Slack bot token can already see.

    Same shape as the admin ``GET /admin/connections/{id}/slack-channels``
    route, resolved against this workspace's own connection.

    Known gap (decision D9, not yet implemented): this returns every channel
    the shared org bot token can see, not just channels the CONNECTING PERSON
    personally belongs to — the plan's identity-grant step ("Sign in with
    Slack" to filter the picker to the owner's own channels) is deferred.
    Until that lands, a workspace owner can technically pick a channel they
    aren't personally a member of, as long as the shared bot is already in
    it. Isolation from OTHER workspaces/org-wide still holds (D10, enforced
    below) — what's missing is the narrower "did *this person* belong to it"
    check.
    """
    conn = _owned_workspace_connection(session.org_id, workspace_id, connection_id)
    if conn.provider != "slack":
        raise HTTPException(
            status_code=400,
            detail=f"Channel listing is only supported for Slack (this connection is {conn.provider!r}).",
        )
    try:
        token = get_live_connection_token(session.org_id, conn.provider, workspace_id=workspace_id)
        channels = list_slack_channels(token)
    except (SourceError, ConfigurationError, OAuthReauthRequiredError) as exc:
        raise_token_http(
            exc, org_id=session.org_id, provider=conn.provider, workspace_id=workspace_id
        )
    return {"channels": channels}


def _connected_slack_channel(conn, channel_id: str):
    """404 unless ``channel_id`` is one this connection actually picked."""
    channel_ids = (conn.source_config or {}).get("channel_ids") or []
    if channel_id not in channel_ids:
        raise HTTPException(status_code=404, detail="Channel is not connected to this workspace")


@router.get("/{workspace_id}/connections/{connection_id}/slack-channels/{channel_id}/members")
def list_workspace_slack_channel_members(
    workspace_id: str,
    connection_id: str,
    channel_id: str,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    """List a connected channel's Slack members, each flagged for invite eligibility.

    Only members who already have a Handbook account in THIS org can be
    invited into the workspace (``workspaces.invite_member`` enforces this
    too — it's checked here again purely so the picker can show, not
    silently omit, "not on Handbook yet" for everyone else). Matching is by
    email, which requires the ``users:read.email`` Slack scope; a member
    Slack won't give an email for is left out entirely (see
    ``list_channel_members``).
    """
    conn = _owned_workspace_connection(session.org_id, workspace_id, connection_id)
    if conn.provider != "slack":
        raise HTTPException(
            status_code=400,
            detail=f"Channel members are only supported for Slack (this connection is {conn.provider!r}).",
        )
    _connected_slack_channel(conn, channel_id)
    try:
        token = get_live_connection_token(session.org_id, conn.provider, workspace_id=workspace_id)
        members = list_channel_members(token, channel_id)
    except (SourceError, ConfigurationError, OAuthReauthRequiredError) as exc:
        raise_token_http(
            exc, org_id=session.org_id, provider=conn.provider, workspace_id=workspace_id
        )

    org_emails = {u.email for u in list_members(session.org_id)}
    workspace_emails = {m["email"] for m in list_workspace_members(workspace_id)}
    return {
        "members": [
            {
                **member,
                "already_org_member": member["email"] in org_emails,
                "already_workspace_member": member["email"] in workspace_emails,
            }
            for member in members
        ]
    }


@router.post("/{workspace_id}/connections/{connection_id}/slack-channels/{channel_id}/invite-members")
def invite_workspace_slack_channel_members(
    workspace_id: str,
    connection_id: str,
    channel_id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
    settings: ApiSettings = Depends(ApiSettings.from_env),
):
    """Bulk-invite a subset of a connected channel's members into this workspace.

    Reuses the exact same ``invite_member`` an owner would trigger one email
    at a time from the members panel — this is only a bulk front-end for it,
    not a new privilege. An email with no matching org account is reported
    back as skipped rather than silently dropped or auto-created (see the
    Slack Integration Plan's member-invite decision: never create new org
    accounts from this flow).
    """
    conn = _owned_workspace_connection(session.org_id, workspace_id, connection_id)
    if conn.provider != "slack":
        raise HTTPException(status_code=400, detail="Member invite is only supported for Slack")
    _connected_slack_channel(conn, channel_id)

    emails = [
        (e or "").strip().lower() for e in (body.get("emails") or []) if isinstance(e, str)
    ]
    invited, skipped = [], []
    for email in emails:
        try:
            invite_member(workspace_id, session.org_id, session.user_id, email)
        except NotFoundError:
            skipped.append(email)
            continue
        invited.append(email)
        _notify_workspace_invite(workspace_id, session, email, background_tasks, settings)
    return {"invited": invited, "skipped_not_org_member": skipped}


@router.put("/{workspace_id}/connections/{connection_id}/config")
def put_connection_config(
    workspace_id: str,
    connection_id: str,
    body: dict,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    """Set the ingestion scope for a workspace's personal connection.

    Same shape as the admin ``PUT /admin/connections/{id}/config`` route
    (Google Drive folder_url, Slack channel_ids) but resolved against this
    workspace's connections, never the org-wide ones. Decision D10: a channel
    already claimed by another connection (org-wide or a sibling workspace)
    for this org is rejected here, since ``validate_slack_channels`` only
    checks Slack-side visibility — the cross-connection dedupe is ours to
    enforce.
    """
    conn = _owned_workspace_connection(session.org_id, workspace_id, connection_id)
    if conn.provider not in ("google", "slack"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Ingestion scope configuration is only supported for Google "
                f"Drive and Slack (this connection is {conn.provider!r})."
            ),
        )

    if conn.provider == "google":
        folder_url = bounded(
            (body.get("folder_url") or "").strip(),
            field="folder_url",
            limit=MAX_URL_CHARS,
        )
        try:
            folder_id = extract_drive_folder_id(folder_url)
            token = get_live_connection_token(session.org_id, conn.provider, workspace_id=workspace_id)
            config = validate_drive_folder(token, folder_id)
            swapped = folder_id_changed(
                session.org_id, conn.provider, config["folder_id"], workspace_id=workspace_id
            )
            set_connection_config(session.org_id, conn.provider, config, workspace_id=workspace_id)
        except (ConfigurationError, SourceError, OAuthReauthRequiredError) as exc:
            raise_token_http(
                exc, org_id=session.org_id, provider=conn.provider, workspace_id=workspace_id
            )
        purged = 0
        if swapped:
            purged = purge_provider_documents(
                session.org_id, conn.provider, workspace_id=workspace_id
            )
        return {
            "connection_id": connection_id,
            "provider": conn.provider,
            "config": config,
            "folder_changed": swapped,
            "documents_purged": purged,
        }

    # provider == "slack"
    channel_ids = body.get("channel_ids") or []
    if not isinstance(channel_ids, list) or not all(isinstance(c, str) for c in channel_ids):
        raise HTTPException(status_code=400, detail="channel_ids must be a list of channel id strings")

    try:
        token = get_live_connection_token(session.org_id, conn.provider, workspace_id=workspace_id)
        config = validate_slack_channels(token, channel_ids)
        conflict = find_slack_channel_conflict(
            session.org_id, config["channel_ids"], exclude_workspace_id=workspace_id
        )
        if conflict is not None:
            raise ConfigurationError(
                "One or more of these channels is already connected elsewhere "
                "in your organization (Company Sources or another space). "
                "Each channel can only be connected in one place."
            )
        swapped = slack_channels_changed(
            session.org_id, conn.provider, config["channel_ids"], workspace_id=workspace_id
        )
        join_public_channels(token, config["channel_ids"])
        set_connection_config(session.org_id, conn.provider, config, workspace_id=workspace_id)
    except (ConfigurationError, SourceError, OAuthReauthRequiredError) as exc:
        raise_token_http(
            exc, org_id=session.org_id, provider=conn.provider, workspace_id=workspace_id
        )

    purged = 0
    if swapped:
        purged = purge_provider_documents(
            session.org_id, conn.provider, workspace_id=workspace_id
        )
    return {
        "connection_id": connection_id,
        "provider": conn.provider,
        "config": config,
        "channels_changed": swapped,
        "documents_purged": purged,
    }


@router.post("/{workspace_id}/connections/{connection_id}/refresh-scope")
def refresh_workspace_connection_scope(
    workspace_id: str,
    connection_id: str,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    """Re-read which repositories this WORKSPACE's installation may see.

    Owner-only, like every other operation that changes what a workspace can
    read — an ordinary member can ask questions but must not be able to widen the
    workspace's data scope. Otherwise identical to the admin route: the repo list
    is stored rather than re-fetched per question, so it needs an explicit refresh
    when the owner edits the installation on GitHub.
    """
    conn = _owned_workspace_connection(session.org_id, workspace_id, connection_id)
    if conn.provider != "github":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Repository scope refresh only applies to GitHub "
                f"(this connection is {conn.provider!r})."
            ),
        )

    try:
        scope = refresh_installation_scope(session.org_id, workspace_id)
        note_live_success(
            session.org_id, "github", workspace_id=workspace_id
        )
    except (ConfigurationError, SourceError, OAuthReauthRequiredError) as exc:
        raise_token_http(
            exc,
            org_id=session.org_id,
            provider=conn.provider,
            workspace_id=workspace_id,
        )

    return {
        "connection_id": connection_id,
        "provider": "github",
        "account_login": scope.account_login,
        "repository_selection": scope.repository_selection,
        "repo_count": len(scope.repos),
        "repos": [
            {
                "full_name": repo.full_name,
                "description": repo.description,
                "topics": list(repo.topics),
            }
            for repo in scope.repos
        ],
    }



@router.get("/{workspace_id}/connections/{connection_id}/health")
def workspace_connection_health(
    workspace_id: str,
    connection_id: str,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(get_workspace_role),
):
    """Probe stored credentials for this space (GitHub on-load equivalent)."""
    conn = _owned_workspace_connection(session.org_id, workspace_id, connection_id)
    try:
        get_live_connection_token(
            session.org_id, conn.provider, workspace_id=workspace_id
        )
        note_live_success(
            session.org_id, conn.provider, workspace_id=workspace_id
        )
    except (ConfigurationError, SourceError, OAuthReauthRequiredError) as exc:
        raise_token_http(
            exc,
            org_id=session.org_id,
            provider=conn.provider,
            workspace_id=workspace_id,
        )
    return {
        "connection_id": connection_id,
        "provider": conn.provider,
        "status": "ok",
        "needs_reauth": False,
    }


@router.get("/{workspace_id}/connections/{connection_id}/changes")
def connection_changes(
    workspace_id: str,
    connection_id: str,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(get_workspace_role),
):
    conn = _owned_workspace_connection(session.org_id, workspace_id, connection_id)
    _reject_if_github(conn.provider, "Change checking")
    try:
        token = get_live_connection_token(session.org_id, conn.provider, workspace_id=workspace_id)
        config = get_connection_config(session.org_id, conn.provider, workspace_id=workspace_id)
        adapter = build_source_adapter(conn.provider, token=token, config=config)
        report = detect_source_changes(
            adapter, session.org_id, provider=conn.provider, workspace_id=workspace_id
        )
        note_live_success(
            session.org_id, conn.provider, workspace_id=workspace_id
        )
    except (ConfigurationError, SourceError, OAuthReauthRequiredError) as exc:
        raise_token_http(
            exc,
            org_id=session.org_id,
            provider=conn.provider,
            workspace_id=workspace_id,
        )

    return {
        "connection_id": connection_id,
        "new_count": report.new_count,
        "updated_count": report.updated_count,
        "removed_count": report.removed_count,
        "unchanged_count": report.unchanged_count,
        "remote_total": report.remote_total,
        "has_changes": report.has_changes,
    }




@router.delete("/{workspace_id}/connections/{connection_id}")
def delete_workspace_connection(
    workspace_id: str,
    connection_id: str,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    """Disconnect a personal space source and purge its indexed docs."""
    _owned_workspace_connection(session.org_id, workspace_id, connection_id)
    try:
        result = disconnect_connection(
            session.org_id, connection_id, workspace_id=workspace_id
        )
    except ConfigurationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "disconnected", **result}


@router.post("/{workspace_id}/connections/{connection_id}/ingest")
def trigger_ingest(
    workspace_id: str,
    connection_id: str,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    conn = _owned_workspace_connection(session.org_id, workspace_id, connection_id)
    _reject_if_github(conn.provider, "Syncing")
    if has_active_job(session.org_id, connection_id):
        raise HTTPException(
            status_code=409, detail="A sync is already in progress for this connection"
        )
    try:
        job_id = enqueue(session.org_id, connection_id, workspace_id=workspace_id)
    except JobAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id, "status": "queued"}


@router.get("/{workspace_id}/jobs")
def get_jobs(workspace_id: str, session: SessionClaims = Depends(get_session), _role: str = Depends(get_workspace_role)):
    return [job_payload(j) for j in list_jobs(session.org_id, workspace_id=workspace_id)]


@router.get("/{workspace_id}/jobs/{job_id}")
def get_job_status(
    workspace_id: str,
    job_id: str,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(get_workspace_role),
):
    job = get_job(session.org_id, job_id)
    if job is None or job.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="No such job for this workspace")
    return job_payload(job)
