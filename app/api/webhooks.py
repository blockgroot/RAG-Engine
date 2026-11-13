"""Push receivers: a provider says something changed, we flag a sync.

A handler only flags the connection and, after answering, queues its sync
(`autosync._flag_and_start`); never ingests inline, and answers inside the
provider's deadline. A push carries nothing trusted, but each signature is
still verified (a forged flag spends a tenant's quota). Every route is 404
until its secret is set, except Notion's verification handshake.
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
        if not request_sync_external(provider, external_id):
            # Signed and fresh but about no connection: the id we stored differs.
            logger.warning("webhooks: %s event for %r matched no connection", provider, external_id)
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
    """Notion events for the whole public integration.

    Notion first POSTs an unsigned ``verification_token``; it is logged for the
    operator to paste into Notion and ``NOTION_WEBHOOK_VERIFICATION_TOKEN``, and
    is the HMAC key for every event. Events are refused until it is set.
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

    # `workspace_id` is the OAuth exchange's `external_workspace_id`.
    # Logged (ids and type only) so "did Notion call us?" has an answer.
    entity = payload.get("entity") if isinstance(payload.get("entity"), dict) else {}
    logger.info(
        "notion webhook: %s for %s %s in workspace %s",
        payload.get("type"), entity.get("type"), entity.get("id"), payload.get("workspace_id"),
    )
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

    ``Linear-Signature`` is hex HMAC-SHA256 of the raw body; ``webhookTimestamp``
    must be within a minute (no replay).
    """
    secret = WebhookSettings.from_env().linear_secret
    if not secret:
        raise _not_found()
    body = await request.body()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not linear_signature or not hmac.compare_digest(expected, linear_signature):
        logger.warning("linear webhook: bad signature (header %s)", "present" if linear_signature else "missing")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad signature")
    payload = _json(body)
    try:
        sent_at = int(payload.get("webhookTimestamp") or 0)
    except (TypeError, ValueError):
        sent_at = 0
    if abs(time.time() * 1000 - sent_at) > _LINEAR_MAX_SKEW_MS:
        logger.warning("linear webhook: stale delivery, %.0fs old", (time.time() * 1000 - sent_at) / 1000)
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
    """A Drive ``changes.watch`` notification (empty body).

    The channel token we chose is the proof. An unknown channel still gets 200,
    else Google retries forever.
    """
    if not WebhookSettings.from_env().drive_push_base_url:
        raise _not_found()
    # `sync` is the channel-opened handshake; any other state counts as a change.
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
