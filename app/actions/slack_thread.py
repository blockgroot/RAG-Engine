"""The Slack thread a request was asked in, read live (``thread_context``).

Agent mode writes through MCP only; this is what is left of Slack here: a
request made inside a channel thread drafts from THAT thread, read with the
bot's token, as one source exactly as visible as its channel.
"""

from __future__ import annotations

import httpx

from ..core.exceptions import ActionError
from .base import Audience, Source

API = "https://slack.com/api"
_TIMEOUT = 15.0
#: Pages read when finding a channel or its members (200 per page). Past this
#: the answer is "can't check", never a guess.
MAX_PAGES = 10
#: Members whose emails are read for an audience. Larger rooms are unknown.
MEMBER_LIMIT = 500
#: Messages read from the thread a request was asked in.
THREAD_LIMIT = 50
def _get(token: str, method: str, params: dict) -> dict:
    try:
        response = httpx.get(f"{API}/{method}", params=params, timeout=_TIMEOUT,
                             headers={"Authorization": f"Bearer {token}"})
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ActionError("Could not reach Slack.", cause=exc) from exc
    if not body.get("ok"):
        raise ActionError(f"Slack: {body.get('error') or 'refused'}")
    return body


def _pages(token: str, method: str, params: dict, key: str) -> tuple[list, bool]:
    """Every item of a paged Slack list, and whether it was complete."""
    items: list = []
    cursor = ""
    for _ in range(MAX_PAGES):
        body = _get(token, method, {**params, "limit": 200, **({"cursor": cursor} if cursor else {})})
        items += body.get(key) or []
        cursor = (body.get("response_metadata") or {}).get("next_cursor") or ""
        if not cursor:
            return items, True
    return items, False


def _members(token: str, channel_id: str) -> tuple[list[str], bool]:
    return _pages(token, "conversations.members", {"channel": channel_id}, "members")


def channel_audience(token: str, channel: dict, members: list[str] | None = None) -> Audience:
    """Public channel: public. Private: its members' emails, or unknown."""
    if not channel.get("is_private"):
        return Audience(public=True)
    if members is None:
        members, complete = _members(token, channel["id"])
        if not complete:
            return Audience(members=None)
    if len(members) > MEMBER_LIMIT:
        return Audience(members=None)
    users, complete = _pages(token, "users.list", {}, "members")
    if not complete:
        return Audience(members=None)
    emails = {u.get("id"): ((u.get("profile") or {}).get("email") or "") for u in users}
    return Audience(members=tuple(
        emails.get(m, "") for m in members if not _is_bot(users, m)))


def _is_bot(users: list[dict], member_id: str) -> bool:
    return any(u.get("id") == member_id and (u.get("is_bot") or u.get("id") == "USLACKBOT")
               for u in users)


def thread_context(token: str, channel_id: str, thread_ts: str) -> tuple[list[Source], str]:
    """The thread a request was asked in, read live, as ``(sources, passages)``.

    Searching the channel for "about this thread" matched whatever else was
    discussed there, and a thread started minutes ago is not synced at all. So
    a request made inside a thread drafts from THAT thread: at most
    ``THREAD_LIMIT`` messages, the bot's own left out, and a cut-off said so.
    It is one source, as visible as its channel: public, or its members
    (unknown members count as nobody, so every reader is an outsider).
    Nothing readable is ``([], "")``.
    """
    from ..security.untrusted import scrub_untrusted_text

    try:
        body = _get(token, "conversations.replies",
                    {"channel": channel_id, "ts": thread_ts, "limit": THREAD_LIMIT})
        channel = _get(token, "conversations.info", {"channel": channel_id}).get("channel") or {}
    except ActionError:
        return [], ""
    lines = [scrub_untrusted_text(m.get("text") or "").strip()
             for m in (body.get("messages") or [])[:THREAD_LIMIT]
             if not m.get("bot_id") and m.get("subtype") != "bot_message"]
    lines = [line for line in lines if line]
    if not lines:
        return [], ""
    if body.get("has_more"):
        lines.append(f"(Only the first {THREAD_LIMIT} messages of this thread were read.)")
    audience = channel_audience(token, channel)
    try:
        link = _get(token, "chat.getPermalink",
                    {"channel": channel_id, "message_ts": thread_ts}).get("permalink")
    except ActionError:
        link = None
    title = f"#{channel.get('name') or channel_id} thread"
    source = Source(f"slack-thread:{channel_id}:{thread_ts}", title, audience.public,
                    tuple(m.lower() for m in (audience.members or ()) if m), link)
    return [source], f"[{title}]\n" + "\n".join(lines)


#: Messages read from a channel for "what was discussed in #x today".
CHANNEL_LIMIT = 200
#: The longest period a channel is read for, in days.
MAX_CHANNEL_DAYS = 30


def channel_context(token: str, name: str, days: int, email: str) -> tuple[list[Source], str] | None:
    """A channel's messages over the last ``days``, read live, as
    ``(sources, passages)``: "summarise what was discussed in #hb-testing
    today" searched the index by meaning, found one message of five, and
    missed what was posted after the last sync. At most ``CHANNEL_LIMIT``
    messages, oldest first, the bot's own left out, and a cut-off said so.

    Read only when the asker may: a public channel, or a private one they
    are a member of (by email); otherwise ``([], "")``. ``None`` when the
    channel cannot be read here (no such channel, the bot is not in it): the
    caller falls back to the indexed search."""
    import time

    from ..security.untrusted import scrub_untrusted_text

    wanted = name.strip().lstrip("#").casefold()
    try:
        channels, _ = _pages(token, "conversations.list",
                             {"types": "public_channel,private_channel", "exclude_archived": "true"}, "channels")
    except ActionError:
        return None
    channel = next((c for c in channels if (c.get("name") or "").casefold() == wanted), None)
    if channel is None:
        return None
    audience = channel_audience(token, channel)
    viewers = tuple(m.lower() for m in (audience.members or ()) if m)
    if not audience.public and email.lower() not in viewers:
        return [], ""
    days = max(1, min(int(days or 1), MAX_CHANNEL_DAYS))
    try:
        body = _get(token, "conversations.history", {"channel": channel["id"], "limit": CHANNEL_LIMIT,
                                                      "oldest": f"{time.time() - days * 86400:.0f}"})
        users, _ = _pages(token, "users.list", {}, "members")
    except ActionError:
        return None
    names = {u.get("id"): ((u.get("profile") or {}).get("real_name") or u.get("name") or "") for u in users}

    def when(m: dict) -> str:
        # Dated, so "today" is the model's to pick from a week it was given.
        return time.strftime("%b %d %H:%M UTC", time.gmtime(float(m.get("ts") or 0)))

    lines = [f"{when(m)} {names.get(m.get('user'), 'Someone')}: {scrub_untrusted_text(m.get('text') or '').strip()}"
             for m in reversed(body.get("messages") or [])
             if not m.get("bot_id") and m.get("subtype") != "bot_message" and (m.get("text") or "").strip()]
    if not lines:
        return [], ""
    if body.get("has_more"):
        lines.insert(0, f"(Only the latest {CHANNEL_LIMIT} messages were read.)")
    now = time.strftime("%b %d %H:%M UTC", time.gmtime())
    title = f"#{channel.get('name')}, last {days} day{'s' if days > 1 else ''}, read at {now}"
    source = Source(f"slack-channel:{channel['id']}", f"#{channel.get('name')}", audience.public, viewers)
    return [source], f"[{title}]\n" + "\n".join(lines)
