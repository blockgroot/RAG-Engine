"""Slack through its regular Web API, with the person's own user token: a post
is theirs, never the bot's (the bot only answers in threads).

Channels and people are named the way people name them ("#general",
"priya@acme.com", "Priya Shah") and resolved here, so a draft's target is the
asker's own words rather than an id the model was shown. Every walk is
bounded and a cut list says so.
"""

from __future__ import annotations

import json

import re

import httpx

from .base import HostedTool, ToolResult, VendorAuthError, VendorError, bounded

API = "https://slack.com/api/"
_PAGES = 5          # x 200 per page
_AUTH_ERRORS = {"invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive"}
_ID = re.compile(r"^[CGDU][A-Z0-9]{6,}$")


def _call(token: str, method: str, **params) -> dict:
    try:
        r = httpx.post(API + method, headers={"Authorization": f"Bearer {token}"},
                       data={k: v for k, v in params.items() if v is not None}, timeout=30)
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise VendorError("Slack did not answer.", cause=exc) from exc
    if not data.get("ok"):
        error = str(data.get("error") or "unknown_error")
        if error in _AUTH_ERRORS:
            raise VendorAuthError("Sign in to Slack again.")
        raise VendorError(f"Slack refused it: {error}")
    return data


def _channels(token: str, mine: bool = False) -> tuple[list[dict], bool]:
    """Channels this person can see (``mine``: is a member of), and whether
    the walk was cut short. Slack fills pages unevenly (archived channels are
    dropped after the page is cut), so a large workspace's full list can run
    out before a channel; the member list is short and is where they post."""
    found, cursor = [], None
    for _ in range(_PAGES):
        page = _call(token, "users.conversations" if mine else "conversations.list",
                     types="public_channel,private_channel", exclude_archived="true", limit=200, cursor=cursor)
        found += page.get("channels") or []
        cursor = (page.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return found, False
    return found, True


def _channel_id(token: str, channel: str) -> tuple[str, str]:
    """``(id, shown name)`` for "#general", "general" or an id."""
    ref = channel.strip()
    if _ID.match(ref):
        return ref, ref
    name = ref.lstrip("#").lower()
    for mine in (True, False):
        channels, cut = _channels(token, mine)
        for c in channels:
            if (c.get("name") or "").lower() == name:
                return c["id"], f"#{c['name']}"
    more = " (only the first 1,000 channels were checked)" if cut else ""
    raise VendorError(f"There's no channel named #{name} that you can post in{more}.")


def _people(token: str) -> tuple[list[dict], bool]:
    found, cursor = [], None
    for _ in range(_PAGES):
        page = _call(token, "users.list", limit=200, cursor=cursor)
        found += [m for m in page.get("members") or [] if not m.get("deleted") and not m.get("is_bot")]
        cursor = (page.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return found, False
    return found, True


def _person(token: str, person: str) -> dict:
    """One person by id, email or exact name; two people with that name is refused."""
    ref = person.strip().lstrip("@")
    if _ID.match(ref) and ref.startswith("U"):
        return _call(token, "users.info", user=ref)["user"]
    if "@" in ref:
        return _call(token, "users.lookupByEmail", email=ref)["user"]
    people, cut = _people(token)
    key = ref.lower()
    match = [m for m in people if key in {(m.get("real_name") or "").lower(), (m.get("name") or "").lower(),
                                          ((m.get("profile") or {}).get("display_name") or "").lower()}]
    if len(match) == 1:
        return match[0]
    if match:
        raise VendorError(f"Several people are called {ref}. Use their email.")
    more = " (only the first 1,000 people were checked)" if cut else ""
    raise VendorError(f"There's nobody called {ref} in this Slack{more}. Use their email.")


def _permalink(token: str, channel: str, ts: str) -> str | None:
    try:
        return _call(token, "chat.getPermalink", channel=channel, message_ts=ts).get("permalink")
    except VendorError:
        return None


# -- Tools ----------------------------------------------------------------------------


def list_channels(token: str, args: dict) -> ToolResult:
    query = str(args.get("query") or "").lstrip("#").lower()
    channels, cut = _channels(token)
    rows = [f"#{c['name']} ({'private' if c.get('is_private') else 'public'}, id {c['id']})"
            for c in channels if query in (c.get("name") or "").lower()][:100]
    note = "\n[only the first 1,000 channels were checked]" if cut else ""
    return ToolResult(("\n".join(rows) or "No matching channels.") + note)


def find_person(token: str, args: dict) -> ToolResult:
    query = str(args.get("query") or "").strip().lower()
    if "@" in query:
        m = _call(token, "users.lookupByEmail", email=query)["user"]
        return ToolResult(f"{m.get('real_name')} <{(m.get('profile') or {}).get('email', '')}> (id {m['id']})")
    people, cut = _people(token)
    rows = [f"{m.get('real_name') or m.get('name')} <{(m.get('profile') or {}).get('email', '')}> (id {m['id']})"
            for m in people if query in " ".join(filter(None, (m.get("real_name"), m.get("name"),
                                                                (m.get("profile") or {}).get("display_name")))).lower()][:20]
    note = "\n[only the first 1,000 people were checked]" if cut else ""
    return ToolResult(("\n".join(rows) or "Nobody matches.") + note)


def read_thread(token: str, args: dict) -> ToolResult:
    channel, shown = _channel_id(token, str(args["channel"]))
    replies = _call(token, "conversations.replies", channel=channel, ts=str(args["thread_ts"]), limit=50)
    lines = [f"[{m.get('ts')}] {m.get('user', 'someone')}: {m.get('text', '')}" for m in replies.get("messages") or []]
    return ToolResult(bounded(f"Thread in {shown}:\n" + "\n".join(lines)))


#: Under every message Agent mode sends: the words stay exactly as approved,
#: and whoever reads them sees a tool sent them on the person's behalf.
SENT_WITH = "Sent with Handbook"


def _post(token: str, channel: str, text: str, thread_ts: str | None = None) -> dict:
    """The approved words as they are (``text`` is also the notification), then
    a small grey context line. A section holds 3,000 characters, so a long
    message is several sections."""
    text = bounded(text, 12_000)
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text[i:i + 3000]}}
              for i in range(0, len(text), 3000)]
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": SENT_WITH}]})
    return _call(token, "chat.postMessage", channel=channel, text=text, blocks=json.dumps(blocks),
                 thread_ts=thread_ts or None)


def send_message(token: str, args: dict) -> ToolResult:
    channel, shown = _channel_id(token, str(args["channel"]))
    posted = _post(token, channel, str(args["text"]), args.get("thread_ts"))
    return ToolResult(f"Posted in {shown}.", posted.get("ts"), _permalink(token, channel, posted.get("ts")))


def send_dm(token: str, args: dict) -> ToolResult:
    person = _person(token, str(args["person"]))
    channel = _call(token, "conversations.open", users=person["id"])["channel"]["id"]
    posted = _post(token, channel, str(args["text"]))
    return ToolResult(f"Sent to {person.get('real_name') or person.get('name')}.", posted.get("ts"),
                      _permalink(token, channel, posted.get("ts")))


_TEXT = {"type": "string", "description": "The message, in Slack formatting."}
_CHANNEL = {"type": "string", "description": 'The channel as the person named it, e.g. "#general".'}

TOOLS = [
    HostedTool("list_channels", "List channels", "Channels the person can see, optionally filtered by name.",
               {"properties": {"query": {"type": "string"}}}, True, list_channels),
    HostedTool("find_person", "Find a person", "People in this Slack by name or email.",
               {"properties": {"query": {"type": "string"}}, "required": ["query"]}, True, find_person),
    HostedTool("read_thread", "Read a thread", "The messages of one thread (at most 50).",
               {"properties": {"channel": _CHANNEL, "thread_ts": {"type": "string"}},
                "required": ["channel", "thread_ts"]}, True, read_thread),
    HostedTool("send_message", "Send a message", "Post a message in a channel, as the person, optionally in a thread.",
               {"properties": {"channel": _CHANNEL, "text": _TEXT, "thread_ts": {"type": "string"}},
                "required": ["channel", "text"]}, False, send_message),
    HostedTool("send_dm", "Send a direct message", "Send a direct message to one person, as the person asking.",
               {"properties": {"person": {"type": "string", "description": "Their email or full name."},
                               "text": _TEXT}, "required": ["person", "text"]}, False, send_dm),
]
