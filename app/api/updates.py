"""Admin › Updates: which version this copy runs, what is newer, and (on a
self-hosted copy with the updater) the button that installs it.

Admin-only. Starting an update restarts this API, so the page polls
``/progress`` and must expect it to fail for a minute while that happens.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from ..updates import UpdateError, start_update, update_progress, update_status
from .deps import SessionClaims, require_admin

router = APIRouter(prefix="/admin/updates", tags=["admin"])


@router.get("")
def get_updates(_: SessionClaims = Depends(require_admin)):
    status = update_status()
    return {
        "deployment": status.deployment,
        "current": status.current,
        "latest": status.latest.version if status.latest else None,
        "newer": [asdict(r) for r in reversed(status.newer)],
        "whats_new": asdict(status.whats_new) if status.whats_new else None,
        "can_update": status.can_update,
        "blocked_reason": status.blocked_reason,
    }


@router.post("")
def post_update(body: dict, _: SessionClaims = Depends(require_admin)):
    version = str(body.get("version") or "").strip().lstrip("v")
    try:
        return start_update(version)
    except UpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/progress")
def get_progress(_: SessionClaims = Depends(require_admin)):
    try:
        return update_progress()
    except UpdateError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
