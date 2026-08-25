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
import re

import httpx

from ..core.exceptions import ConfigurationError, SourceError

logger = logging.getLogger(__name__)

_API_BASE = "https://slack.com/api"
_TIMEOUT = 10.0
# Bound the channel-listing walk itself — same "cap the walk, not just what it
# produces" discipline as Google's folder crawl and the Notion fetch-size fix.
# 10 pages * 200/page = 2000 channels, comfortably above any real workspace.
_MAX_LIST_PAGES = 10
# Bound the member walk the same way — a channel with more members than this
# is truncated rather than issuing an unbounded number of users.info calls.
_MAX_CHANNEL_MEMBERS = 500


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


_SAFE_CHANNEL = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def relabel_indexed_channel(
    org_id: str, old: str, new: str, workspace_id: str | None = None
) -> int:
    """Repoint already-indexed Slack content at a channel's NEW name.

    Every Slack chunk begins with a ``Channel: #name`` header this adapter
    writes (``fetch_document``), and each document is titled ``#name: snippet``.
    After a rename those strings still say the old name, so asking about the
    new one retrieves nothing: BM25 has no matching token and the vector side
    only sees a generic "what was discussed" query, so the confidence gate
    refuses. Observed exactly that — "No answer found" for #rag-updates while
    six chunks sat there under #hand-book-testing.

    Only the leading header and the title prefix are rewritten — both authored
    by us. A message BODY that happens to mention the old name is left alone:
    that is what somebody actually wrote, and rewriting history to match a
    rename would be a lie in the record.

    ``content_tsv`` is a GENERATED column, so keyword search corrects itself on
    this UPDATE. Embeddings still encode the old token, which is why this is a
    repair and not a substitute for re-ingesting — but retrieval is hybrid, so
    the keyword side is enough to surface the chunk again immediately.

    Returns how many chunks were repointed.
    """
    if not (_SAFE_CHANNEL.match(old) and _SAFE_CHANNEL.match(new)) or old == new:
        return 0

    from ..db.connection import get_connection

    old_header, new_header = f"Channel: #{old}", f"Channel: #{new}"
    try:
        with get_connection() as conn:
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

    ``source_config.channel_names`` is a snapshot written when someone picked
    the channels, and a Slack rename touches no message id or timestamp — so
    change detection correctly reports "up to date" while every label goes on
    showing the old name: the suggested questions, a report's coverage note,
    "#old-name" inside an activity item.

    Channel *ids* are stable across a rename, which is what makes this safe:
    only labels move, never which channels are connected. A channel missing
    from the listing keeps its stored name — a name we once knew beats a bare
    id.

    Lives here (with the other Slack API knowledge) rather than in the API
    layer, because the consumers are the ingest worker and the scheduler, and
    a domain module must not import from ``app/api``. Returns the
    ``(old, new)`` pairs that changed. Best-effort: a Slack or DB failure
    returns ``[]``, since a stale label must never break a sync, a report, or
    the page that was only checking for changes.
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
    # Labels in the CONFIG are only half of it: already-indexed content still
    # carries the old name in the header we wrote, so a question about the new
    # name retrieves nothing until this runs.
    for old, new in renamed:
        relabel_indexed_channel(org_id, old, new, workspace_id)
    return renamed


def join_public_channels(
    token: str, channel_ids: list[str], known: dict[str, dict] | None = None
) -> None:
    """Auto-join any of ``channel_ids`` that are public and not yet joined.

    Decision D7: public channels may be auto-joined (``conversations.join``
    needs no human action); private channels have no such API — a human must
    ``/invite`` the bot in Slack, which this function does not attempt.
    Failures are swallowed per-channel (best-effort) so one already-archived
    or unreachable channel doesn't abort saving the rest of the picker
    selection — the picker's "Ready"/"Invite the bot" badges are the source
    of truth for what actually worked, re-checked on the next listing.

    ``known`` lets a caller that already listed channels (e.g.
    ``validate_slack_channels``, moments earlier in the same request) pass
    that result through instead of paying a second full paginated
    ``conversations.list`` scan for the same data.
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
    """List real (non-bot) members of one channel, with email for matching.

    Returns ``[{"id", "name", "email"}, ...]``. A member is skipped (not an
    error) if they're a bot, deleted, or Slack simply has no email on file for
    them (``profile.email`` absent) — this connector never fails wholesale
    because of one un-matchable Slack account. Requires the ``users:read.email``
    scope; without it every member is skipped rather than raising, so an
    org running on the old scope just sees an empty list until it reconnects.

    Raises:
        SourceError: unexpected Slack/HTTP failure on the channel-membership
            call itself (not on a per-user lookup, which is best-effort).
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

    Returns ``{"channel_ids": [...], "channel_names": {id: name}}`` suitable
    for ``set_connection_config`` (same shape convention as
    ``validate_drive_folder``'s ``{"folder_id", "folder_name"}``).

    ``known`` lets a caller pass an already-fetched channel listing (see
    ``join_public_channels``) instead of triggering a second full
    ``conversations.list`` scan in the same request.

    Raises:
        ConfigurationError: an empty selection, or a channel id Slack doesn't
            currently return for this token (never silently drops it).
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
