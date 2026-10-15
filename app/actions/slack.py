"""Slack action: post a message, as the Handbook bot, naming who asked.

The one action that does not run on the person's own token. Slack forbids an
app posting as a person ("DON'T impersonate a user"), so the bot posts and the
message opens with "<@person> asked Handbook to post this". Because the tool
cannot check the person's rights here, these checks are ours:

* Asked from a CHANNEL, the only place it may post is that same thread: a
  mention in one room never posts into another.
* Otherwise the channel must be named by the asker, the bot must already be in
  it (it never joins one), and the asker must be a member -- read from Slack
  with a bounded walk; a list cut short is "can't check", never "yes".
* A public channel is a public destination, so private sources never go there.
* The text is escaped like every bot reply, so a drafted ``<!channel>`` or a
  disguised ``<url|text>`` link arrives as inert text, and nobody is pinged.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..auth.credentials import get_live_connection_token
from ..core.exceptions import ActionError, ActionOutcomeUnknown, ActionRejected
from .base import Action, ActionContext, ActionResult, ActionTarget, Audience, Proposal, Source
from .draft import clean_body, require_named

API = "https://slack.com/api"
_TIMEOUT = 15.0
#: Pages read when finding a channel or its members (200 per page). Past this
#: the answer is "can't check", never a guess.
MAX_PAGES = 10
#: Members whose emails are read for an audience. Larger rooms are unknown.
MEMBER_LIMIT = 500
#: Messages read from the thread a request was asked in.
THREAD_LIMIT = 50
_NO_UNFURL = {"unfurl_links": False, "unfurl_media": False}


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


def _channel_by_name(token: str, name: str) -> dict:
    wanted = name.lstrip("#").casefold().strip()
    channels, _complete = _pages(token, "conversations.list", {
        "types": "public_channel,private_channel", "exclude_archived": "true"}, "channels")
    for channel in channels:
        if (channel.get("name") or "").casefold() == wanted:
            return channel
    raise ActionRejected(f"I can't see a channel called #{wanted}. Invite @Handbook to it first.")


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


def _mrkdwn(text: str) -> str:
    """The draft as Slack formatting, escaped first: the bot's own answer
    formatter, so ``**bold**`` and ``# Heading`` read as Slack shows them and a
    drafted ``<!channel>`` or ``<url|text>`` still arrives as inert text."""
    from ..api.slack_events import _to_slack_mrkdwn  # local: api imports this module

    return _to_slack_mrkdwn(text)


def escape(text: str) -> str:
    """Slack's own escaping, so nothing in a draft becomes a ping or a link label."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


class SlackPostMessage(Action):
    name = "slack.post_message"
    provider = "slack"
    label = "Slack: post a message"
    attribution = "as Handbook, naming you"
    tool = {
        "type": "function",
        "function": {
            "name": "slack_post_message",
            "description": (
                "Post a message in Slack. From a channel, it can only reply in the "
                "current thread; elsewhere, in a channel the user named."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_words": {
                        "type": "string",
                        "description": (
                            "The exact words from the user's message that ask for this "
                            "action, copied verbatim (for example 'post in')."
                        ),
                    },
                    "channel": {
                        "type": "string",
                        "description": "The channel exactly as the user wrote it, e.g. #eng. "
                                       "Leave empty to reply in the current thread.",
                    },
                    "text": {"type": "string", "description": "The message to post."},
                },
                "required": ["request_words", "text"],
            },
        },
    }

    # The stored text is "<@asker> asked Handbook to post this:" + a newline +
    # the escaped message. Only the message is the person's to edit; the
    # first line, naming who asked, is rebuilt as it was.
    editable_fields = ("body",)

    def editable(self, payload: dict[str, Any]) -> dict[str, str]:
        return {"body": unescape(payload.get("text", "").partition("\n")[2])}

    def edit(self, payload: dict[str, Any], fields: dict[str, Any], *,
             asked: str | None = None) -> dict[str, Any]:
        if set(fields) != {"body"}:
            raise ActionRejected(
                "Only the message can be edited. To post somewhere else, cancel and ask again."
            )
        text = fields["body"] if isinstance(fields["body"], str) else ""
        body = clean_body(text, text if asked is None else asked, ())
        if not body:
            raise ActionRejected("The draft is empty.")
        lead = payload.get("text", "").partition("\n")[0]
        return {**payload, "text": f"{lead}\n{_mrkdwn(body)}"}

    def token_for(self, org_id: str, user_id: str) -> str:
        """The company's bot token: Slack lets an app post, never a person."""
        return get_live_connection_token(org_id, "slack", workspace_id=None)

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        surface = ctx.surface or {}
        asker = surface.get("slack_user")
        if not asker:
            raise ActionRejected(
                "Handbook doesn't know your Slack account yet. Ask the Handbook bot to "
                "do something in Slack once, then try again."
            )
        named = (args.get("channel") or "").strip() if isinstance(args.get("channel"), str) else ""
        thread_ts = None
        if surface.get("kind") == "slack_channel":
            here = surface["channel"]
            channel = _get(ctx.token, "conversations.info", {"channel": here}).get("channel") or {}
            if named and named.lstrip("#").casefold() != (channel.get("name") or "").casefold():
                raise ActionRejected(
                    "From a channel I can only reply in this thread. DM me to post somewhere else."
                )
            thread_ts = surface.get("thread_ts")
            members = None
        else:
            channel = _channel_by_name(ctx.token, require_named(named, ctx.question, "Slack channel"))
            if not channel.get("is_member"):
                raise ActionRejected(f"Invite @Handbook to #{channel.get('name')} first.")
            members, complete = _members(ctx.token, channel["id"])
            if not complete or asker not in members:
                raise ActionRejected(f"You need to be a member of #{channel.get('name')} to post there.")
        body = clean_body(args.get("text"), ctx.question, [s.uri for s in ctx.sources if s.uri])
        if not body:
            raise ActionRejected("The draft is empty.")
        where = f"#{channel.get('name') or channel['id']}" + (" (this thread)" if thread_ts else "")
        return Proposal(
            action=self.name,
            target=ActionTarget("slack_channel", channel["id"], where),
            payload={"text": f"<@{asker}> asked Handbook to post this:\n{_mrkdwn(body)}",
                     "thread_ts": thread_ts},
            audience=channel_audience(ctx.token, channel, members),
        )

    def execute(self, key, target, payload, token) -> ActionResult:
        message = {"channel": target["external_id"], "text": payload["text"], **_NO_UNFURL}
        if payload.get("thread_ts"):
            message["thread_ts"] = payload["thread_ts"]
        try:
            response = httpx.post(f"{API}/chat.postMessage", json=message, timeout=_TIMEOUT,
                                  headers={"Authorization": f"Bearer {token}"})
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ActionOutcomeUnknown("Slack did not answer.", cause=exc) from exc
        if not body.get("ok"):
            raise ActionError(f"Slack refused the post: {body.get('error')}.")
        ts = body.get("ts") or ""
        try:
            link = _get(token, "chat.getPermalink",
                        {"channel": target["external_id"], "message_ts": ts}).get("permalink")
        except ActionError:
            link = None  # posted; only the link is missing
        return ActionResult(ts, link)
