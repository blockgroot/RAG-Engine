"""Slack channel picker plumbing (Phase 3 of the Slack Integration Plan).

Slack's OAuth grant screen doesn't let the installer pick channels the way
GitHub's own install screen does (see the plan's §5) — so a second step is
needed after connect: list what the bot can already see, let the admin pick,
then save that as the ingestion scope. Mirrors ``google_drive_utils.py``'s
role (kept standalone, no adapter import, so channel listing stays usable
without pulling in the full ``SlackAdapter``).

Decision D2 (never "connect all channels" implicitly) means channel
membership is always something the admin actively chooses here, not
inferred. Decision D7 (no auto-join for private channels; public channels
may be auto-joined once selected) is implemented by ``join_public_channels``.
"""

from __future__ import annotations

import logging

import httpx

from ..core.exceptions import ConfigurationError, SourceError

logger = logging.getLogger(__name__)

_API_BASE = "https://slack.com/api"
_TIMEOUT = 10.0
# Bound the channel-listing walk itself — same "cap the walk, not just what it
# produces" discipline as Google's folder crawl and the Notion fetch-size fix.
# 10 pages * 200/page = 2000 channels, comfortably above any real workspace.
_MAX_LIST_PAGES = 10


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
    """List public + private channels this bot token can already see.

    Returns ``[{"id", "name", "is_private", "is_member"}, ...]``. A private
    channel the bot hasn't been invited to never appears here at all — Slack
    only returns channels the token has *some* visibility into, which for a
    private channel means membership already exists (there is no "private
    channel that exists but I can't see it" listing in Slack's API).

    Raises:
        SourceError: unexpected Slack/HTTP failure.
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


def join_public_channels(token: str, channel_ids: list[str]) -> None:
    """Auto-join any of ``channel_ids`` that are public and not yet joined.

    Decision D7: public channels may be auto-joined (``conversations.join``
    needs no human action); private channels have no such API — a human must
    ``/invite`` the bot in Slack, which this function does not attempt.
    Failures are swallowed per-channel (best-effort) so one already-archived
    or unreachable channel doesn't abort saving the rest of the picker
    selection — the picker's "Ready"/"Invite the bot" badges are the source
    of truth for what actually worked, re-checked on the next listing.
    """
    known = {c["id"]: c for c in list_slack_channels(token)}
    for channel_id in channel_ids:
        info = known.get(channel_id)
        if info is None or info["is_private"] or info["is_member"]:
            continue
        try:
            _post(token, "conversations.join", {"channel": channel_id})
        except SourceError as exc:
            logger.warning("slack.join_channel failed for %s: %s", channel_id, exc)


def validate_slack_channels(token: str, channel_ids: list[str]) -> dict:
    """Confirm every requested channel id is one the bot can currently see.

    Returns ``{"channel_ids": [...], "channel_names": {id: name}}`` suitable
    for ``set_connection_config`` (same shape convention as
    ``validate_drive_folder``'s ``{"folder_id", "folder_name"}``).

    Raises:
        ConfigurationError: an empty selection, or a channel id Slack doesn't
            currently return for this token (never silently drops it).
    """
    if not channel_ids:
        raise ConfigurationError("Select at least one Slack channel to connect.")

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
