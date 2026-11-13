"""Drive push notifications: one ``changes.watch`` channel per Google connection.

Google POSTs an EMPTY notification when the account's change log moves; the
handler only flags a sync (``autosync.request_sync_connection``).

* Channels expire (at most a week) and are never auto-renewed: the tick
  re-watches inside ``RENEW_BEFORE``, new channel first, so overlap only
  duplicates a flag.
* No signature: the proof is our channel token, stored as SHA-256.
* The change log is the whole account's, so any change flags a sync.
  ponytail: filter `changes.list` against the folder if that wastes syncs.

Off unless ``DRIVE_PUSH_BASE_URL`` is set; any failure leaves the hourly poll.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from ..config.settings import WebhookSettings
from ..db.connection import get_connection

logger = logging.getLogger(__name__)

_API = "https://www.googleapis.com/drive/v3"
_TIMEOUT = 10.0
#: Google's ceiling for a `changes` channel is one week.
WATCH_TTL = timedelta(days=7)
#: Re-watch this long before expiry, so a tick that runs late still renews in time.
RENEW_BEFORE = timedelta(days=1)
#: Connections (re)watched per tick. Bounded because each is two Drive calls.
MAX_PER_TICK = 20
#: Where Google delivers. Relative to `DRIVE_PUSH_BASE_URL`.
PATH = "/webhooks/google"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _due(limit: int) -> list[tuple[str, str, str | None, str | None, str | None]]:
    """Google connections with a folder whose channel is missing or expiring.

    ``(connection_id, org_id, workspace_id, old_channel_id, old_resource_id)``.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id::text, c.org_id::text, c.workspace_id::text,
                   w.channel_id, w.resource_id
              FROM oauth_connections c
              LEFT JOIN drive_watch_channels w ON w.connection_id = c.id
             WHERE c.provider = 'google'
               AND NOT c.needs_reauth
               AND c.source_config ->> 'folder_id' IS NOT NULL
               AND (w.connection_id IS NULL OR w.expires_at < now() + %s)
             ORDER BY w.expires_at NULLS FIRST
             LIMIT %s
            """,
            (RENEW_BEFORE, limit),
        ).fetchall()
    return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]


def _watch(token: str, address: str, channel_id: str, secret: str) -> tuple[str, datetime]:
    """Open a channel. Returns ``(resource_id, expires_at)``; raises on failure."""
    headers = {"Authorization": f"Bearer {token}"}
    start = httpx.get(
        f"{_API}/changes/startPageToken",
        params={"supportsAllDrives": "true"},
        headers=headers, timeout=_TIMEOUT,
    )
    start.raise_for_status()
    expires = datetime.now(timezone.utc) + WATCH_TTL
    response = httpx.post(
        f"{_API}/changes/watch",
        params={
            # Required though the reference omits it; the page token does not expire.
            "pageToken": start.json()["startPageToken"],
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        },
        json={
            "id": channel_id,
            "type": "web_hook",
            "address": address,
            "token": secret,
            "expiration": int(expires.timestamp() * 1000),
        },
        headers=headers, timeout=_TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    granted = body.get("expiration")
    if granted:
        expires = datetime.fromtimestamp(int(granted) / 1000, tz=timezone.utc)
    return body["resourceId"], expires


def _stop(token: str, channel_id: str, resource_id: str) -> None:
    """Best-effort: an unstopped channel just expires, and its pings are ignored."""
    try:
        httpx.post(
            f"{_API}/channels/stop",
            json={"id": channel_id, "resourceId": resource_id},
            headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT,
        )
    except Exception:  # noqa: BLE001
        logger.debug("drive_watch: stopping channel %s failed", channel_id, exc_info=True)


def ensure_watches(settings: WebhookSettings | None = None) -> int:
    """Open or renew channels that need one; returns channels opened. Never raises
    (a failed watch is just polled)."""
    settings = settings or WebhookSettings.from_env()
    if not settings.drive_push_base_url:
        return 0
    from ..auth.credentials import get_live_connection_token

    address = settings.drive_push_base_url + PATH
    opened = 0
    for connection_id, org_id, workspace_id, old_channel, old_resource in _due(MAX_PER_TICK):
        try:
            token = get_live_connection_token(org_id, "google", workspace_id)
            channel_id = uuid.uuid4().hex
            secret = secrets.token_urlsafe(32)
            resource_id, expires = _watch(token, address, channel_id, secret)
            with get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO drive_watch_channels
                        (connection_id, channel_id, resource_id, token_hash, expires_at)
                    VALUES (%s::uuid, %s, %s, %s, %s)
                    ON CONFLICT (connection_id) DO UPDATE SET
                        channel_id = EXCLUDED.channel_id,
                        resource_id = EXCLUDED.resource_id,
                        token_hash = EXCLUDED.token_hash,
                        expires_at = EXCLUDED.expires_at,
                        created_at = now()
                    """,
                    (connection_id, channel_id, resource_id, _hash(secret), expires),
                )
            opened += 1
        except Exception:  # noqa: BLE001 - this connection stays on the poll
            logger.warning("drive_watch: could not watch connection %s", connection_id, exc_info=True)
            continue
        # The new channel is live and stored, so the old one can go.
        if old_channel and old_resource:
            _stop(token, old_channel, old_resource)
    return opened


def connection_for_notification(channel_id: str, channel_token: str) -> str | None:
    """The connection a notification is about, or ``None`` if it proves nothing.
    Matched on channel id AND token hash, so a replaced channel's late
    notifications are ignored."""
    if not channel_id or not channel_token:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT connection_id::text, token_hash FROM drive_watch_channels WHERE channel_id = %s",
            (channel_id,),
        ).fetchone()
    if row is None or not secrets.compare_digest(row[1], _hash(channel_token)):
        return None
    return row[0]
