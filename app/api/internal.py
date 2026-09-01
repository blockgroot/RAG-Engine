"""Externally-driven background tick.

Why this endpoint exists at all
------------------------------
A free-tier Render web service spins down after ~15 minutes with **no inbound
HTTP request**. Process activity does not count: an in-process timer thread
does not keep the instance alive, so every background loop in this codebase
(ingestion queue, activity scheduler, auto-sync) silently stops shortly after
the last user closes the tab — and comes back only when someone visits.

That makes "we sync every 6 hours" false on the deployment we actually run on.
A free external cron (cron-job.org, UptimeRobot, a GitHub Actions schedule)
hitting this endpoint fixes it, and fixes it *properly*: the same request that
wakes the instance also does the work, so there is no ping-then-hope window
where the box is awake but nothing has been triggered.

Auth
----
A shared secret in a header, not a session: the caller is a cron job, not a
person, and it has no org. Unset secret **disables the route** rather than
leaving it open — an unauthenticated tick is a free way for anyone to spend
every tenant's provider quota, and "nobody will find the URL" is not an access
control.

Compared on the raw bytes with ``secrets.compare_digest`` so a wrong secret
costs the same time as a right one.
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
    """Run one background pass: reap stuck jobs, sync due connections, run
    due schedulers.

    Idempotent and safe to call often: every step is already guarded (the
    interval floor, the unique active-job index, the scheduler's claim). A
    minute-granularity cron is fine; the work happens only when something is
    actually due.
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
