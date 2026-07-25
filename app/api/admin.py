"""Admin router: domain allowlist, OAuth connections, ingestion jobs (Phase 13c).

Every route requires an admin session (``require_admin``) and takes ``org_id``
exclusively from that session — never from the URL or body — so an admin can
only ever manage their OWN organization's domains, connections, and jobs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import list_connections
from ..auth import domains as domains_mod
from ..core.exceptions import ConfigurationError
from ..jobs import enqueue, get_job, list_jobs
from .deps import SessionClaims, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/domains")
def register_domain(body: dict, session: SessionClaims = Depends(require_admin)):
    domain = (body.get("domain") or "").strip().lower()
    if not domain:
        raise HTTPException(status_code=400, detail="A domain is required")
    try:
        record = domains_mod.register_domain(session.org_id, domain)
    except ConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": record.id,
        "domain": record.domain,
        "auto_join_enabled": record.auto_join_enabled,
    }


@router.get("/domains")
def list_domains(session: SessionClaims = Depends(require_admin)):
    return [
        {
            "id": d.id,
            "domain": d.domain,
            "auto_join_enabled": d.auto_join_enabled,
        }
        for d in domains_mod.list_domains(session.org_id)
    ]


@router.post("/domains/{domain_id}/auto-join")
def set_auto_join(
    domain_id: str, body: dict, session: SessionClaims = Depends(require_admin)
):
    enabled = bool(body.get("enabled", False))
    ok = domains_mod.set_auto_join(session.org_id, domain_id, enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="No such domain for this organization")
    return {"auto_join_enabled": enabled}


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
