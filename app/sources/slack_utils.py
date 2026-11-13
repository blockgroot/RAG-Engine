"""Slack channel picker plumbing, and the bot's Slack Web API calls.

Slack's install screen picks no channels, so after connect the admin picks from
what the bot can see; never "all channels" implicitly. Public channels may be
auto-joined; private ones need a human ``/invite``. No adapter import.
"""

from __future__ import annotations

import logging
import re

import httpx

from ..core.exceptions import ConfigurationError, SourceError

logger = logging.getLogger(__name__)

_API_BASE = "https://slack.com/api"
_TIMEOUT = 10.0
# Bound the listing walk: 10 pages * 200 = 2000 channels.
_MAX_LIST_PAGES = 10
# Bound the member walk; a larger channel is truncated.
_MAX_CHANNEL_MEMBERS = 500


# The one spelling of a channel's `documents.tags` entry, shared by ingest and ask.
_CHANNEL_TAG_PREFIX = "slack:channel:"


# Previews OFF on every bot message: Slack fetching a steered URL is a zero-click leak.
_NO_UNFURL = {"unfurl_links": False, "unfurl_media": False}


def channel_tag(channel_id: str) -> str:
    """The `documents.tags` label for one Slack channel."""
    return f"{_CHANNEL_TAG_PREFIX}{channel_id}"


def post_message(
    token: str, channel: str, text: str, thread_ts: str | None = None
) -> str | None:
    """Post one message, optionally into a thread. Never raises (the caller runs
    after Slack was acknowledged). Returns the message ``ts`` for `update_message`,
    or ``None`` when the post failed."""
    payload: dict = {"channel": channel, "text": text, **_NO_UNFURL}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        response = httpx.post(
            f"{_API_BASE}/chat.postMessage",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
        body = response.json()
        if not body.get("ok"):
            logger.warning("slack.chat_postMessage failed: %s", body.get("error"))
            return None
        return body.get("ts")
    except Exception as exc:  # noqa: BLE001 - a failed reply is never fatal
        logger.warning("slack.chat_postMessage error: %s", exc)
        return None


def update_message(token: str, channel: str, ts: str, text: str) -> bool:
    """Replace the text of a message we posted, to show progress. Never raises.

    `assistant.threads.setStatus` only works in assistant threads, not the
    `app_mention`/DM surfaces this bot answers; editing needs only `chat:write`.
    """
    try:
        response = httpx.post(
            f"{_API_BASE}/chat.update",
            json={"channel": channel, "ts": ts, "text": text, **_NO_UNFURL},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
        body = response.json()
        if not body.get("ok"):
            logger.warning("slack.chat_update failed: %s", body.get("error"))
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("slack.chat_update error: %s", exc)
        return False


#: Upload timeout: bytes go to Slack's upload host, slower than an API call, still bounded.
_UPLOAD_TIMEOUT = 30.0


def upload_file(
    token: str,
    channel: str,
    data: bytes,
    *,
    filename: str,
    title: str,
    alt_text: str,
    initial_comment: str | None = None,
    thread_ts: str | None = None,
) -> str:
    """Share a file into a channel or thread via Slack's two-step external upload.
    Never raises. Returns ``"ok"``, ``"missing_scope"`` (reconnect for `files:write`)
    or ``"error"``; the caller falls back to text unless ``"ok"``."""
    auth = {"Authorization": f"Bearer {token}"}
    step = "files.getUploadURLExternal"
    try:
        body = httpx.post(
            f"{_API_BASE}/files.getUploadURLExternal",
            data={"filename": filename, "length": str(len(data)), "alt_txt": alt_text[:1000]},
            headers=auth,
            timeout=_TIMEOUT,
        ).json()
        if body.get("ok"):
            file_id = body.get("file_id")
            step = "upload"
            sent = httpx.post(body["upload_url"], content=data, timeout=_UPLOAD_TIMEOUT)
            if sent.status_code != 200:
                logger.warning("slack.file upload failed: HTTP %s", sent.status_code)
                return "error"
            step = "files.completeUploadExternal"
            payload: dict = {"files": [{"id": file_id, "title": title}], "channel_id": channel}
            if thread_ts:
                payload["thread_ts"] = thread_ts
            if initial_comment:
                payload["initial_comment"] = initial_comment
            body = httpx.post(
                f"{_API_BASE}/files.completeUploadExternal",
                json=payload,
                headers=auth,
                timeout=_TIMEOUT,
            ).json()
        if body.get("ok"):
            return "ok"
        error = body.get("error")
        logger.warning("slack.%s failed: %s", step, error)
        return "missing_scope" if error == "missing_scope" else "error"
    except Exception as exc:  # noqa: BLE001 - a failed upload falls back to text
        logger.warning("slack.%s error: %s", step, exc)
        return "error"


def _get(token: str, method: str, params: dict, *, timeout: float = _TIMEOUT) -> dict:
    try:
        response = httpx.get(
            f"{_API_BASE}/{method}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceError(f"Slack API call to {method} failed: {exc}", cause=exc) from exc

    data = response.json()
    if not data.get("ok"):
        raise SourceError(f"Slack API call to {method} failed: {data.get('error')}")
    return data


def _post(token: str, method: str, data: dict, *, timeout: float = _TIMEOUT) -> dict:
    try:
        response = httpx.post(
            f"{_API_BASE}/{method}",
            data=data,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SourceError(f"Slack API call to {method} failed: {exc}", cause=exc) from exc

    payload = response.json()
    if not payload.get("ok"):
        raise SourceError(f"Slack API call to {method} failed: {payload.get('error')}")
    return payload


def list_slack_channels(token: str) -> list[dict]:
    """List public + private channels this bot token can see:
    ``[{"id", "name", "is_private", "is_member"}, ...]``. A private channel appears
    only when the bot is already in it. Raises ``SourceError`` on failure.
    """
    channels: list[dict] = []
    cursor = None
    for _ in range(_MAX_LIST_PAGES):
        params = {
            "types": "public_channel,private_channel",
            "limit": 200,
            "exclude_archived": "true",
        }
        if cursor:
            params["cursor"] = cursor
        data = _get(token, "conversations.list", params)
        for ch in data.get("channels", []):
            channels.append(
                {
                    "id": ch["id"],
                    "name": ch.get("name") or ch["id"],
                    "is_private": bool(ch.get("is_private")),
                    "is_member": bool(ch.get("is_member")),
                }
            )
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    else:
        logger.warning("slack.list_channels truncated at %s pages", _MAX_LIST_PAGES)
    return channels


_SAFE_CHANNEL = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def relabel_indexed_channel(
    org_id: str, old: str, new: str, workspace_id: str | None = None
) -> int:
    """Repoint already-indexed Slack content at a channel's NEW name.

    Rewrites only the ``Channel: #name`` header and ``#name:`` title prefix we wrote,
    never a message body. ``content_tsv`` is generated, so keyword search recovers at
    once; embeddings keep the old name until re-ingest. Returns chunks repointed.
    """
    if not (_SAFE_CHANNEL.match(old) and _SAFE_CHANNEL.match(new)) or old == new:
        return 0

    from ..db.connection import get_connection

    old_header, new_header = f"Channel: #{old}", f"Channel: #{new}"
    try:
        with get_connection(org_id=org_id) as conn:
            rows = conn.execute(
                """
                UPDATE chunks AS c
                SET content = %s || substring(c.content FROM %s)
                FROM documents AS d
                WHERE d.id = c.document_id
                  AND c.org_id = %s
                  AND d.source_provider = 'slack'
                  AND d.workspace_id IS NOT DISTINCT FROM %s
                  AND c.content LIKE %s
                RETURNING 1
                """,
                (
                    new_header,
                    len(old_header) + 1,
                    org_id,
                    workspace_id,
                    old_header + "%",
                ),
            ).fetchall()
            conn.execute(
                """
                UPDATE documents
                SET title = %s || substring(title FROM %s)
                WHERE org_id = %s
                  AND source_provider = 'slack'
                  AND workspace_id IS NOT DISTINCT FROM %s
                  AND title LIKE %s
                """,
                (
                    f"#{new}:",
                    len(f"#{old}:") + 1,
                    org_id,
                    workspace_id,
                    f"#{old}:%",
                ),
            )
    except Exception as exc:  # noqa: BLE001 - a label repair is never fatal
        logger.warning("Could not relabel indexed Slack content: %s", exc)
        return 0

    if rows:
        logger.info(
            "Relabelled %s indexed chunk(s) from #%s to #%s", len(rows), old, new
        )
    return len(rows)


def refresh_channel_names(org_id: str, workspace_id: str | None = None) -> list[tuple[str, str]]:
    """Re-read each connected channel's CURRENT name and persist it.

    A rename moves no message id, so change detection sees "up to date" while labels
    show the old name. Ids are stable, so only labels move; a channel missing from
    the listing keeps its stored name. Returns changed ``(old, new)`` pairs.
    Best-effort: any failure returns ``[]``.
    """
    from ..auth import get_live_connection_token
    from ..auth.credentials import get_connection_config, set_connection_config

    try:
        config = get_connection_config(org_id, "slack", workspace_id=workspace_id) or {}
        channel_ids = list(config.get("channel_ids") or [])
        if not channel_ids:
            return []
        stored = dict(config.get("channel_names") or {})
        token = get_live_connection_token(org_id, "slack", workspace_id=workspace_id)
        live = {c["id"]: c["name"] for c in list_slack_channels(token)}
    except Exception as exc:  # noqa: BLE001 - a label refresh is never fatal
        logger.warning("Slack channel-name refresh failed for org %s: %s", org_id, exc)
        return []

    renamed: list[tuple[str, str]] = []
    updated = dict(stored)
    for channel_id in channel_ids:
        current = live.get(channel_id)
        if not current:
            continue
        previous = stored.get(channel_id)
        if previous and previous != current:
            renamed.append((previous, current))
        if previous != current:
            updated[channel_id] = current

    if updated != stored:
        try:
            set_connection_config(
                org_id, "slack", {**config, "channel_names": updated},
                workspace_id=workspace_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not persist Slack channel names: %s", exc)
            return []
        logger.info(
            "Refreshed Slack channel names for org %s (%s renamed)", org_id, len(renamed)
        )
    # Indexed content also carries the old name in our header: repoint it too.
    for old, new in renamed:
        relabel_indexed_channel(org_id, old, new, workspace_id)
    return renamed


def join_public_channels(
    token: str, channel_ids: list[str], known: dict[str, dict] | None = None
) -> None:
    """Auto-join any of ``channel_ids`` that are public and not yet joined.

    Private channels need a human ``/invite``. Per-channel failures are swallowed;
    the picker's badges show what worked. ``known`` reuses an existing listing.
    """
    if known is None:
        known = {c["id"]: c for c in list_slack_channels(token)}
    for channel_id in channel_ids:
        info = known.get(channel_id)
        if info is None or info["is_private"] or info["is_member"]:
            continue
        try:
            _post(token, "conversations.join", {"channel": channel_id})
        except SourceError as exc:
            logger.warning("slack.join_channel failed for %s: %s", channel_id, exc)


def list_channel_members(token: str, channel_id: str) -> list[dict]:
    """List real (non-bot) members of one channel: ``[{"id", "name", "email"}, ...]``.

    A bot, deleted or email-less member is skipped (needs ``users:read.email``).
    Raises ``SourceError`` only on the membership call itself.
    """
    member_ids: list[str] = []
    cursor = None
    for _ in range(_MAX_LIST_PAGES):
        if len(member_ids) >= _MAX_CHANNEL_MEMBERS:
            logger.warning(
                "slack.list_channel_members truncated at %s members for %s",
                _MAX_CHANNEL_MEMBERS,
                channel_id,
            )
            break
        params = {"channel": channel_id, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = _get(token, "conversations.members", params)
        member_ids.extend(data.get("members", []))
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    members: list[dict] = []
    for user_id in member_ids[:_MAX_CHANNEL_MEMBERS]:
        try:
            info = _get(token, "users.info", {"user": user_id})
        except SourceError as exc:
            logger.warning("slack.users_info failed for %s: %s", user_id, exc)
            continue
        user = info.get("user") or {}
        if user.get("is_bot") or user.get("deleted"):
            continue
        email = (user.get("profile") or {}).get("email")
        if not email:
            continue
        members.append(
            {
                "id": user_id,
                "name": user.get("real_name") or user.get("name") or user_id,
                "email": email.lower(),
            }
        )
    return members


def validate_slack_channels(
    token: str, channel_ids: list[str], known: dict[str, dict] | None = None
) -> dict:
    """Confirm every requested channel id is one the bot can currently see.

    Returns ``{"channel_ids", "channel_names"}`` for ``set_connection_config``.
    ``known`` reuses an existing listing. Raises ``ConfigurationError`` on an empty
    selection or an unseen id (never silently dropped).
    """
    if not channel_ids:
        raise ConfigurationError("Select at least one Slack channel to connect.")

    if known is None:
        known = {c["id"]: c for c in list_slack_channels(token)}
    missing = [cid for cid in channel_ids if cid not in known]
    if missing:
        raise ConfigurationError(
            f"Unknown or inaccessible Slack channel id(s): {', '.join(missing)}. "
            "Refresh the channel list and try again."
        )

    return {
        "channel_ids": list(channel_ids),
        "channel_names": {cid: known[cid]["name"] for cid in channel_ids},
    }
