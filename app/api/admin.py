"""Admin router: member invites, OAuth connections, ingestion jobs (Phase 13c).

Every route requires an admin session (``require_admin``) and takes ``org_id``
exclusively from that session — never from the URL or body — so an admin can
only ever manage their OWN organization's members, connections, and jobs.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from ..auth import (
    create_magic_link_token,
    get_connection_token,
    get_user_by_email,
    invite_member,
    list_connections,
    list_members,
    send_magic_link_email_safe,
)
from ..config.settings import ApiSettings, EmailSettings
from ..ingestion import detect_source_changes
from ..jobs import enqueue, get_job, has_active_job, list_jobs
from ..sources import build_source_adapter
from .deps import SessionClaims, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


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


@router.get("/connections")
def get_connections(session: SessionClaims = Depends(require_admin)):
    return [
        {
            "id": c.id,
            "provider": c.provider,
            "external_workspace_name": c.external_workspace_name,
            "created_at": c.created_at.isoformat(),
        }
        for c in list_connections(session.org_id)
    ]



@router.get("/connections/{connection_id}/changes")
def connection_changes(connection_id: str, session: SessionClaims = Depends(require_admin)):
    """Metadata-only: which remote pages are new/updated/removed vs our store.

    Does not download page bodies or embed — safe to call on Sources page load.
    """
    owned = {c.id: c for c in list_connections(session.org_id)}
    conn = owned.get(connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="No such connection for this organization")

    token = get_connection_token(session.org_id, conn.provider)
    adapter = build_source_adapter(conn.provider, token=token)
    report = detect_source_changes(adapter, session.org_id)
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
    owned_ids = {c.id for c in list_connections(session.org_id)}
    if connection_id not in owned_ids:
        raise HTTPException(status_code=404, detail="No such connection for this organization")

    if has_active_job(session.org_id, connection_id):
        raise HTTPException(
            status_code=409,
            detail="A sync is already in progress for this connection",
        )

    job_id = enqueue(session.org_id, connection_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/jobs")
def get_jobs(session: SessionClaims = Depends(require_admin)):
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
        for j in list_jobs(session.org_id)
    ]


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str, session: SessionClaims = Depends(require_admin)):
    job = get_job(session.org_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such job for this organization")
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
