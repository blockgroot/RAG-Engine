"""Admin router: member invites, OAuth connections, ingestion jobs (Phase 13c).

Every route requires an admin session (``require_admin``) and takes ``org_id``
exclusively from that session — never from the URL or body — so an admin can
only ever manage their OWN organization's members, connections, and jobs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_user_by_email, invite_member, list_connections, list_members
from ..jobs import enqueue, get_job, has_active_job, list_jobs
from .deps import SessionClaims, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/members")
def invite(body: dict, session: SessionClaims = Depends(require_admin)):
    """Directly add a specific email as a member of the caller's org.

    Replaces domain-based auto-join: no domain matching, no eligibility
    resolution — the admin names the exact email. Rejects an email that
    already has an account anywhere, same as signup, since an email is bound
    to its first-resolved org for good.
    """
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if get_user_by_email(email) is not None:
        raise HTTPException(status_code=400, detail="An account already exists for this email")

    user = invite_member(email, session.org_id)
    return {"id": user.id, "email": user.email, "role": user.role}


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
