"""Push receivers: a provider says something changed, we flag a sync.

Every handler here does ONE thing -- stamp ``sync_requested_at`` on the
connection(s) the push is about -- and never ingests inline. The tick reads
and clears the flag, so fifty pushes still make one job (CLAUDE.md §3
Automatic freshness), and each handler answers inside the provider's deadline
(Linear: 5 s; Slack: 3 s, handled in ``slack_events.py``).

A push never carries content we trust: it only moves a sync earlier. That is
why a forged or replayed push is harmless beyond one extra listing diff -- and
why each route still verifies its signature: an unauthenticated flag is a free
way to spend a tenant's provider quota.

Every route is CLOSED (404) until its secret is configured, the
``INTERNAL_TICK_SECRET`` posture. The one exception is Notion's verification
handshake, which by design arrives before the secret exists.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from ..config.settings import WebhookSettings
from ..jobs.autosync import request_sync_connection, request_sync_external
from ..sources import drive_watch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

#: Linear: "We recommend that you verify it's within a minute".
_LINEAR_MAX_SKEW_MS = 60_000


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _flag(provider: str, external_id: str) -> None:
    """Background: never raises, since the provider has already been answered."""
    try:
        request_sync_external(provider, external_id)
    except Exception:  # noqa: BLE001 - a missed flag costs one poll interval
        logger.warning("webhooks: could not flag %s %s", provider, external_id, exc_info=True)


def _json(body: bytes) -> dict:
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Bad JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Bad JSON")
    return payload


# -- Notion -------------------------------------------------------------------


@router.post("/notion")
async def notion_webhook(
    request: Request,
    background: BackgroundTasks,
    x_notion_signature: str | None = Header(default=None),
):
    """Notion events, one subscription for the whole public integration.

    Setup is a handshake Notion runs once: it POSTs ``{"verification_token":
    ...}`` unsigned, a person pastes that token back into the subscription's
    Verify form, and the SAME token is then the HMAC key for every event. So
    the token is logged here for the operator to copy into both Notion and
    ``NOTION_WEBHOOK_VERIFICATION_TOKEN``. Events are refused until it is set.
    """
    body = await request.body()
    payload = _json(body)
    settings = WebhookSettings.from_env()

    token = payload.get("verification_token")
    if token and len(payload) == 1:
        logger.warning(
            "notion webhook: verification token received -- paste it into the "
            "subscription's Verify form and set NOTION_WEBHOOK_VERIFICATION_TOKEN: %s",
            token,
        )
        return {"ok": True}

    secret = settings.notion_verification_token
    if not secret:
        raise _not_found()
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not x_notion_signature or not hmac.compare_digest(expected, x_notion_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad signature")

    # `workspace_id` is "the workspace ID where the event originated from" --
    # the same id the OAuth exchange stored as `external_workspace_id`.
    background.add_task(_flag, "notion", str(payload.get("workspace_id") or ""))
    return {"ok": True}


# -- Linear -------------------------------------------------------------------


@router.post("/linear")
async def linear_webhook(
    request: Request,
    background: BackgroundTasks,
    linear_signature: str | None = Header(default=None),
):
    """Linear data-change events for every workspace that installed the app.

    Configured once on the OAuth application (Linear then creates a webhook
    per authorizing workspace). ``Linear-Signature`` is the bare hex
    HMAC-SHA256 of the raw body, and ``webhookTimestamp`` (ms) must be within
    a minute, which is what stops a captured delivery being replayed.
    """
    secret = WebhookSettings.from_env().linear_secret
    if not secret:
        raise _not_found()
    body = await request.body()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not linear_signature or not hmac.compare_digest(expected, linear_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad signature")
    payload = _json(body)
    try:
        sent_at = int(payload.get("webhookTimestamp") or 0)
    except (TypeError, ValueError):
        sent_at = 0
    if abs(time.time() * 1000 - sent_at) > _LINEAR_MAX_SKEW_MS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Stale delivery")

    # `organizationId` is the workspace the OAuth exchange stored as
    # `external_workspace_id` (`query { organization { id } }`).
    background.add_task(_flag, "linear", str(payload.get("organizationId") or ""))
    return {"ok": True}


# -- Google Drive ---------------------------------------------------------------


@router.post("/google")
async def drive_webhook(
    background: BackgroundTasks,
    x_goog_channel_id: str | None = Header(default=None),
    x_goog_channel_token: str | None = Header(default=None),
    x_goog_resource_state: str | None = Header(default=None),
):
    """A Drive ``changes.watch`` notification. The body is always empty.

    No signature exists; the channel token we chose is the proof
    (``drive_watch.connection_for_notification``). An unknown or unproven
    channel still gets a 200 -- anything else makes Google retry a
    notification we will never accept.
    """
    if not WebhookSettings.from_env().drive_push_base_url:
        raise _not_found()
    # `sync` is the channel-opened handshake, not a change. Google documents the
    # change state as both `change` and `changed`, and says to expect new
    # states, so anything that is not `sync` counts.
    if (x_goog_resource_state or "").lower() == "sync":
        return {"ok": True}
    background.add_task(_flag_drive, x_goog_channel_id or "", x_goog_channel_token or "")
    return {"ok": True}


def _flag_drive(channel_id: str, channel_token: str) -> None:
    try:
        connection_id = drive_watch.connection_for_notification(channel_id, channel_token)
        if connection_id:
            request_sync_connection(connection_id)
    except Exception:  # noqa: BLE001
        logger.warning("webhooks: could not flag a Drive notification", exc_info=True)
