"""Admin router: member invites, OAuth connections, ingestion jobs (Phase 13c).

Every route requires an admin session (``require_admin``) and takes ``org_id``
exclusively from that session — never from the URL or body — so an admin can
only ever manage their OWN organization's members, connections, and jobs.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..auth import (
    create_magic_link_token,
    get_connection_config,
    get_live_connection_token,
    get_user_by_email,
    invite_member,
    list_connections,
    list_members,
    revoke_user_sessions,
    send_magic_link_email_safe,
    set_connection_config,
)
from ..config.settings import ApiSettings, EmailSettings
from ..core.exceptions import ConfigurationError, SourceError
from ..githublive import refresh_installation_scope
from ..ingestion import detect_source_changes
from ..jobs import enqueue, get_job, has_active_job, list_jobs
from .serialize import job_payload
from ..sources import (
    build_source_adapter,
    extract_drive_folder_id,
    search_drive_folders,
    validate_drive_folder,
)
from .deps import SessionClaims, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


def _owned_connection(org_id: str, connection_id: str):
    """Return the connection if it belongs to ``org_id``, else raise 404."""
    owned = {c.id: c for c in list_connections(org_id)}
    conn = owned.get(connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="No such connection for this organization")
    return conn


def _build_connection_adapter(org_id: str, provider: str):
    """Resolve a live token + source_config into a SourceAdapter."""
    token = get_live_connection_token(org_id, provider)
    config = get_connection_config(org_id, provider)
    return build_source_adapter(provider, token=token, config=config)


def _reject_if_github(provider: str, what: str) -> None:
    """GitHub connections have no ingestion — refuse sync-shaped operations.

    Notion and Drive are *ingested* (fetch → chunk → embed → store). GitHub is
    read **live** at question time and never indexed, so every sync-shaped
    endpoint is meaningless for it.

    This matters most for ``/ingest``: without this guard it would enqueue a job
    the worker cannot run, and the admin would see a queued job fail minutes
    later with an obscure "Unknown source type" error. Refusing up front states
    the actual truth — there is nothing to sync because nothing is stored.
    """
    if provider == "github":
        raise HTTPException(
            status_code=400,
            detail=(
                f"{what} does not apply to GitHub. GitHub repositories are read "
                "live when a question is asked, so there is nothing to sync or "
                "configure here — the repositories in scope are chosen on "
                "GitHub's own installation screen."
            ),
        )


@router.post("/members")
def invite(
    body: dict,
    background_tasks: BackgroundTasks,
    session: SessionClaims = Depends(require_admin),
    settings: ApiSettings = Depends(ApiSettings.from_env),
):
    """Add ``email`` as a member of the caller's org and email a magic link.

    Creates the account immediately (so they appear in Team), then sends the
    same single-use sign-in link used by signup / login. With
    ``EMAIL_SENDER=console`` the link is also echoed as ``dev_link``.
    """
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if get_user_by_email(email) is not None:
        raise HTTPException(status_code=400, detail="An account already exists for this email")

    user = invite_member(email, session.org_id)

    # Account exists even if outbound email fails — don't roll back the invite.
    token = create_magic_link_token(email)
    base = (settings.frontend_url or "").rstrip("/")
    link = f"{base}/verify?token={token}"
    email_settings = EmailSettings.from_env()
    background_tasks.add_task(send_magic_link_email_safe, email, link)
    dev_link = link if email_settings.sender == "console" else None

    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "dev_link": dev_link,
    }


@router.get("/members")
def get_members(session: SessionClaims = Depends(require_admin)):
    return [
        {"id": u.id, "email": u.email, "role": u.role, "created_at": u.created_at.isoformat()}
        for u in list_members(session.org_id)
    ]


@router.post("/members/{user_id}/revoke-sessions")
def revoke_member_sessions(user_id: str, session: SessionClaims = Depends(require_admin)):
    """Force-log-out a member (or admin) by invalidating all their sessions."""
    try:
        revoke_user_sessions(user_id, session.org_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "revoked", "user_id": user_id}


@router.get("/connections")
def get_connections(session: SessionClaims = Depends(require_admin)):
    return [
        {
            "id": c.id,
            "provider": c.provider,
            "external_workspace_name": c.external_workspace_name,
            "created_at": c.created_at.isoformat(),
            "source_config": c.source_config,
        }
        for c in list_connections(session.org_id)
    ]


@router.get("/connections/{connection_id}/config")
def get_connection_config_route(
    connection_id: str, session: SessionClaims = Depends(require_admin)
):
    """Return this connection's stored ingestion-scope config (non-secret)."""
    conn = _owned_connection(session.org_id, connection_id)
    config = get_connection_config(session.org_id, conn.provider)
    return {"connection_id": connection_id, "provider": conn.provider, "config": config or {}}


@router.get("/connections/{connection_id}/drive-folders")
def search_connection_drive_folders(
    connection_id: str,
    q: str = "",
    session: SessionClaims = Depends(require_admin),
):
    """List Drive folders this connection's account can see (folder-picker dropdown).

    Powers a search-as-you-type folder picker in the Sources UI so connecting
    a folder no longer requires copy-pasting its URL — see
    ``google_drive_utils.search_drive_folders``.
    """
    conn = _owned_connection(session.org_id, connection_id)
    if conn.provider != "google":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Folder search is only supported for Google Drive "
                f"(this connection is {conn.provider!r})."
            ),
        )
    try:
        token = get_live_connection_token(session.org_id, conn.provider)
        folders = search_drive_folders(token, q)
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"folders": folders}


@router.put("/connections/{connection_id}/config")
def put_connection_config(
    connection_id: str,
    body: dict,
    session: SessionClaims = Depends(require_admin),
):
    """Set Google Drive folder scope for a connection.

    Body: ``{"folder_url": "<Drive folder URL or bare id>"}``. Parses the id,
    validates via Drive ``files.get`` (accessible + actually a folder), then
    stores ``{folder_id, folder_name}`` on the connection.
    """
    conn = _owned_connection(session.org_id, connection_id)
    if conn.provider != "google":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Folder configuration is only supported for Google Drive "
                f"(this connection is {conn.provider!r})."
            ),
        )

    folder_url = (body.get("folder_url") or "").strip()
    if not folder_url:
        raise HTTPException(
            status_code=400,
            detail="folder_url is required (a Drive folder URL or folder id).",
        )

    try:
        folder_id = extract_drive_folder_id(folder_url)
        token = get_live_connection_token(session.org_id, conn.provider)
        config = validate_drive_folder(token, folder_id)
        set_connection_config(session.org_id, conn.provider, config)
    except ConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "connection_id": connection_id,
        "provider": conn.provider,
        "config": config,
    }


@router.post("/connections/{connection_id}/refresh-scope")
def refresh_connection_scope(
    connection_id: str, session: SessionClaims = Depends(require_admin)
):
    """Re-read which repositories a GitHub installation is allowed to see.

    The authorized repo list is stored at connect time rather than fetched per
    question (it changes only when an admin edits the installation on GitHub, so
    re-listing every time would spend rate limit and latency re-learning
    something static). The cost of that choice is staleness: a repo added on
    GitHub afterwards isn't visible to us until someone refreshes. This endpoint
    is that refresh — it is the GitHub analogue of Drive's "check for changes",
    for scope rather than content.
    """
    conn = _owned_connection(session.org_id, connection_id)
    if conn.provider != "github":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Repository scope refresh only applies to GitHub "
                f"(this connection is {conn.provider!r})."
            ),
        )

    try:
        scope = refresh_installation_scope(session.org_id)
    except (ConfigurationError, SourceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

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


@router.get("/connections/{connection_id}/changes")
def connection_changes(connection_id: str, session: SessionClaims = Depends(require_admin)):
    """Metadata-only: which remote pages are new/updated/removed vs our store.

    Does not download page bodies or embed — safe to call on Sources page load.
    """
    conn = _owned_connection(session.org_id, connection_id)
    _reject_if_github(conn.provider, "Change checking")

    try:
        adapter = _build_connection_adapter(session.org_id, conn.provider)
        report = detect_source_changes(adapter, session.org_id, provider=conn.provider)
    except ConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "connection_id": connection_id,
        "new_count": report.new_count,
        "updated_count": report.updated_count,
        "removed_count": report.removed_count,
        "unchanged_count": report.unchanged_count,
        "remote_total": report.remote_total,
        "has_changes": report.has_changes,
    }


@router.post("/connections/{connection_id}/ingest")
def trigger_ingest(connection_id: str, session: SessionClaims = Depends(require_admin)):
    # enqueue() doesn't itself check the connection belongs to this org, so
    # verify via list_connections (already org-scoped) before enqueuing —
    # never let an admin enqueue a job against a connection_id that isn't
    # actually theirs.
    conn = _owned_connection(session.org_id, connection_id)
    _reject_if_github(conn.provider, "Syncing")

    if has_active_job(session.org_id, connection_id):
        raise HTTPException(
            status_code=409,
            detail="A sync is already in progress for this connection",
        )

    job_id = enqueue(session.org_id, connection_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs")
def get_jobs(session: SessionClaims = Depends(require_admin)):
    return [job_payload(j) for j in list_jobs(session.org_id)]


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str, session: SessionClaims = Depends(require_admin)):
    job = get_job(session.org_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job for this organization")
    return job_payload(job)
