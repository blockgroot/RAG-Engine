"""Externally driven background tick (``POST`` from a cron).

A free Render service sleeps after ~15 idle minutes and in-process timers do
not keep it awake, so an outside cron calls this: the request that wakes it
also does the work. Auth is a shared header secret compared with
``secrets.compare_digest``; unset, the route is disabled, since an open tick
spends every tenant's provider quota.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, status

from ..config.settings import AutoSyncSettings
from ..jobs.worker import run_external_tick

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/tick")
def tick(x_tick_secret: str | None = Header(default=None)):
    """Run one pass: reap stuck jobs, sync due connections, run due schedulers.

    Idempotent (each step is guarded), so a per-minute cron is fine.
    """
    settings = AutoSyncSettings.from_env()
    if not settings.tick_secret:
        # 404, not 403: an unconfigured endpoint should not advertise that it
        # exists and is merely locked.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if not x_tick_secret or not secrets.compare_digest(
        x_tick_secret, settings.tick_secret
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bad secret")

    result = run_external_tick()
    logger.info("External tick: %s", result)
    return {"status": "ok", **result}
