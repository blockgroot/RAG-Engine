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

from fastapi import APIRouter, Depends, HTTPException

from ..auth import (
    get_connection_config,
    get_live_connection_token,
    list_connections,
    set_connection_config,
)
from ..core.exceptions import ConfigurationError, NotFoundError, SourceError
from ..githublive import refresh_installation_scope
from ..ingestion import detect_source_changes
from ..jobs import enqueue, get_job, has_active_job, list_jobs
from ..sources import (
    build_source_adapter,
    extract_drive_folder_id,
    search_drive_folders,
    validate_drive_folder,
)
from ..db.connection import get_connection
from ..workspaces import create_workspace, invite_member, list_my_workspaces, list_workspace_members
from .deps import SessionClaims, get_session, get_workspace_role, require_workspace_owner
from .setup_status import content_setup_status

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.post("")
def create(body: dict, session: SessionClaims = Depends(get_session)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="A workspace name is required")
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
        **status,
    }


def _workspace_github_connected(org_id: str, workspace_id: str) -> bool:
    """True only when this workspace has its OWN github connection row."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM oauth_connections "
            "WHERE org_id = %s AND provider = 'github' AND workspace_id = %s",
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


@router.post("/{workspace_id}/members")
def invite(
    workspace_id: str,
    body: dict,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    try:
        invite_member(workspace_id, session.org_id, session.user_id, email)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "invited", "email": email}


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
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"folders": folders}


@router.put("/{workspace_id}/connections/{connection_id}/config")
def put_connection_config(
    workspace_id: str,
    connection_id: str,
    body: dict,
    session: SessionClaims = Depends(get_session),
    _role: str = Depends(require_workspace_owner),
):
    """Set Google Drive folder scope for a workspace's personal connection.

    Same shape as the admin ``PUT /admin/connections/{id}/config`` route
    (Google Drive requires an in-scope folder up front) but resolved against
    this workspace's connections, never the org-wide ones.
    """
    conn = _owned_workspace_connection(session.org_id, workspace_id, connection_id)
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
            status_code=400, detail="folder_url is required (a Drive folder URL or folder id)."
        )
    try:
        folder_id = extract_drive_folder_id(folder_url)
        token = get_live_connection_token(session.org_id, conn.provider, workspace_id=workspace_id)
        config = validate_drive_folder(token, folder_id)
        set_connection_config(session.org_id, conn.provider, config, workspace_id=workspace_id)
    except ConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"connection_id": connection_id, "provider": conn.provider, "config": config}


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
    job_id = enqueue(session.org_id, connection_id, workspace_id=workspace_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/{workspace_id}/jobs")
def get_jobs(workspace_id: str, session: SessionClaims = Depends(get_session), _role: str = Depends(get_workspace_role)):
    return [
        {
            "id": j.id,
            "connection_id": j.connection_id,
            "status": j.status,
            "doc_count": j.doc_count,
            "error": j.error,
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            "created_at": j.created_at.isoformat(),
        }
        for j in list_jobs(session.org_id, workspace_id=workspace_id)
    ]


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
    return {
        "id": job.id,
        "connection_id": job.connection_id,
        "status": job.status,
        "doc_count": job.doc_count,
        "error": job.error,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat(),
    }
