"""Slack action: post a message.

From the WEB it runs on the person's own Slack sign-in, like every other tool:
the message is theirs, and Slack itself refuses a channel they are not in. The
channel must be one they named and are a member of (``is_member`` on their own
token's listing), whether public or private.

Asked INSIDE Slack, the Handbook bot replies there, and the message opens with
"<@person> asked Handbook to post this". The bot cannot check the person's
rights, so these checks are ours:

* Asked from a CHANNEL, the only place it may post is that same thread: a
  mention in one room never posts into another.
* From a DM, the channel must be named by the asker, the bot must already be in
  it (it never joins one), and the asker must be a member -- read from Slack
  with a bounded walk; a list cut short is "can't check", never "yes".

Either way a public channel is a public destination, so private sources never
go there, and the text is escaped like every bot reply, so a drafted
``<!channel>`` or a disguised ``<url|text>`` link arrives as inert text, and
nobody is pinged.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import httpx

from ..auth.credentials import get_live_connection_token
from ..core.exceptions import (
    ActionError,
    ActionOutcomeUnknown,
    ActionRejected,
    ConfigurationError,
    OAuthReauthRequiredError,
)
from .base import (Action, ActionContext, ActionResult, ActionTarget, Audience, Proposal, Source,
                   details)
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
#: A Slack message link: .../archives/<channel>/p<ts without the dot>, and for
#: a reply in a thread, ?thread_ts=<root ts>.
_MESSAGE_LINK = re.compile(
    r"https://[\w.-]+\.slack\.com/archives/(?P<channel>[CDG][A-Z0-9]+)/p(?P<ts>\d{10})(?P<frac>\d{6})"
    r"(?:\?\S*?thread_ts=(?P<root>\d{10}\.\d{6}))?"
)


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


def _bot_scope(org_id: str) -> str | None:
    """Which Slack connection's bot posts: company-wide first, else a space's.
    Any of them is the same bot in the same Slack; WHAT a draft may read is
    decided by the surface, never by this."""
    from ..db.connection import get_connection

    # ponytail: assumes one Slack workspace per company; with several, pick
    # the connection whose team matches the event's.
    with get_connection() as conn:
        row = conn.execute(
            "SELECT workspace_id::text FROM oauth_connections WHERE org_id = %s::uuid "
            "AND provider = 'slack' AND NOT needs_reauth ORDER BY workspace_id NULLS FIRST LIMIT 1",
            (org_id,),
        ).fetchone()
    if row is None:
        raise ConfigurationError("Slack isn't connected to Handbook.")
    return row[0]


def _channel_by_name(token: str, name: str, hint: str = "") -> dict:
    wanted = name.lstrip("#").casefold().strip()
    channels, _complete = _pages(token, "conversations.list", {
        "types": "public_channel,private_channel", "exclude_archived": "true"}, "channels")
    for channel in channels:
        if (channel.get("name") or "").casefold() == wanted:
            return channel
    raise ActionRejected(f"I can't see a channel called #{wanted}.{hint}")


_SENT_WITH = "_Sent with Handbook_"
_NO_DM_PERSON = "Reconnect Slack in Handbook (Account → Agent) to allow direct messages. Nothing was sent."
#: Inside Slack the BOT sends, so it is the app's scope, not the person's sign-in.
_NO_DM_BOT = ("The Handbook Slack app can't send direct messages yet: an admin needs to add the "
              "im:write bot scope and reinstall it. Nothing was sent.")


def _post(token: str, method: str, body: dict, *, missing: str = _NO_DM_PERSON) -> dict:
    """A Slack write that must succeed before the message is sent."""
    try:
        response = httpx.post(f"{API}/{method}", json=body, timeout=_TIMEOUT,
                              headers={"Authorization": f"Bearer {token}"})
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ActionError("Could not reach Slack. Nothing was sent.", cause=exc) from exc
    if data.get("error") == "missing_scope":
        raise ActionError(missing)
    if not data.get("ok"):
        raise ActionError(f"Slack: {data.get('error') or 'refused'}. Nothing was sent.")
    return data


def _person(token: str, wanted: str) -> dict:
    """The one person in this Slack whose email, name or display name the asker
    wrote. Bots, deleted accounts and a cut-off list are never a match."""
    users, complete = _pages(token, "users.list", {}, "members")
    folded = wanted.casefold().lstrip("@")
    found = [u for u in users if not u.get("deleted") and not _is_bot(users, u.get("id", ""))
             and folded in {(u.get("name") or "").casefold(),
                            ((u.get("profile") or {}).get("real_name") or "").casefold(),
                            ((u.get("profile") or {}).get("display_name") or "").casefold(),
                            ((u.get("profile") or {}).get("email") or "").casefold()}]
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise ActionRejected(f"More than one person in Slack matches {wanted!r}. Use their email.")
    raise ActionRejected(f"I can't find {wanted!r} in your Slack" + ("." if complete else
                         ", and your Slack has more people than I could list. Use their email."))


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
    label = "Slack: post a message or DM"
    attribution = "as Handbook, naming you"
    tool = {
        "type": "function",
        "function": {
            "name": "slack_post_message",
            "description": (
                "Post a message in Slack. From a channel, it can only reply in the "
                "current thread; elsewhere, in a channel the user named, as a reply in "
                "a thread whose link they pasted, or as a direct message to a person they named."
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
                    "thread": {"type": "string", "description":
                               "To reply in a thread: the Slack message link exactly as the user pasted it."},
                    "person": {"type": "string", "description":
                               "To send a direct message: the person exactly as the user wrote them "
                               "(name or email)."},
                    "text": {"type": "string", "description": "The message to post."},
                },
                "required": ["request_words", "text"],
            },
        },
    }

    # Posted as the person (``as_person``), the stored text is the message.
    # Posted by the bot, it is "<@asker> asked Handbook to post this:" + a
    # newline + the message; only the message is the person's to edit, and
    # the first line, naming who asked, is rebuilt as it was.
    editable_fields = ("body",)

    def attribution_for(self, payload: dict[str, Any]) -> str:
        return "as you" if payload.get("as_person") else self.attribution

    def editable(self, payload: dict[str, Any]) -> dict[str, str]:
        text = payload.get("text", "")
        return {"body": unescape(text if payload.get("as_person") else text.partition("\n")[2])}

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
        if payload.get("as_person"):
            return {**payload, "text": _mrkdwn(body)}
        lead = payload.get("text", "").partition("\n")[0]
        return {**payload, "text": f"{lead}\n{_mrkdwn(body)}"}

    def token_for(self, org_id: str, user_id: str, surface: str = "web",
                  payload: dict[str, Any] | None = None) -> str:
        """From the web, the person's own Slack sign-in; inside Slack, the
        company's bot (through any of its Slack connections), replying where
        it was asked. A DM drafted as the person is sent with their sign-in
        wherever it was asked."""
        if surface == "web" or (payload or {}).get("as_person"):
            return super().token_for(org_id, user_id, surface)
        return get_live_connection_token(org_id, "slack", workspace_id=_bot_scope(org_id))

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        surface = ctx.surface or {}
        if surface.get("kind", "web") == "web":
            return self._prepare_as_person(args, ctx)
        asker = surface.get("slack_user")
        if not asker:
            raise ActionRejected(
                "Handbook doesn't know your Slack account yet. Ask the Handbook bot to "
                "do something in Slack once, then try again."
            )
        person_named = (args.get("person") or "").strip() if isinstance(args.get("person"), str) else ""
        if person_named:
            # From a channel the bot only replies in that thread, as for any post.
            if surface.get("kind") == "slack_channel":
                raise ActionRejected("From a channel I can only reply in this thread. DM me to message someone.")
            return self._prepare_bot_dm(person_named, args, ctx, asker)
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
            channel = _channel_by_name(ctx.token, require_named(named, ctx.question, "Slack channel"),
                                       " Invite @Handbook to it first.")
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

    def _prepare_bot_dm(self, wanted: str, args: dict[str, Any], ctx: ActionContext, asker: str) -> Proposal:
        """Asked in a DM with the bot: sent AS the asker when they connected
        their own Slack (Account → Agent), so it arrives from them, noting
        Handbook; otherwise the bot sends it, saying who asked. The audience is
        the two of them either way."""
        try:
            own = super().token_for(ctx.org_id, ctx.user_id)
        except (ConfigurationError, OAuthReauthRequiredError):
            own = None
        if own:
            return self._prepare_as_person({**args, "person": wanted}, replace(ctx, token=own))
        person = _person(ctx.token, require_named(wanted, ctx.question, "person"))
        body = clean_body(args.get("text"), ctx.question, [s.uri for s in ctx.sources if s.uri])
        if not body:
            raise ActionRejected("The draft is empty.")
        name = (person.get("profile") or {}).get("real_name") or person.get("name") or person["id"]
        email = ((person.get("profile") or {}).get("email") or "").lower()
        return Proposal(
            action=self.name,
            target=ActionTarget("slack_user", person["id"], f"Direct message to {name}"),
            payload={"text": f"<@{asker}> asked Handbook to send you this:\n{_mrkdwn(body)}",
                     "thread_ts": None},
            audience=Audience(members=(email, ctx.email.lower())) if email else Audience(members=None),
        )

    def _prepare_as_person(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        """A post from the web, on the person's own token: in a channel they
        named and are in, as a reply in a thread whose link they pasted, or as
        a direct message to a person they named. Their listing shows every
        public channel and only the private ones they belong to; ``is_member``
        is read from it. Slack refuses anything else with their token anyway."""
        def arg(key: str) -> str:
            return (args.get(key) or "").strip() if isinstance(args.get(key), str) else ""

        body = clean_body(args.get("text"), ctx.question, [s.uri for s in ctx.sources if s.uri])
        if not body:
            raise ActionRejected("The draft is empty.")
        payload = {"text": _mrkdwn(body), "thread_ts": None, "as_person": True}
        if arg("person"):
            person = _person(ctx.token, require_named(arg("person"), ctx.question, "person"))
            name = (person.get("profile") or {}).get("real_name") or person.get("name") or person["id"]
            email = ((person.get("profile") or {}).get("email") or "").lower()
            return Proposal(
                action=self.name,
                target=ActionTarget("slack_user", person["id"], f"Direct message to {name}"),
                # Sent as them, so the person reading it knows Handbook wrote it.
                payload={**payload, "footer": _SENT_WITH,
                         "details": details(("Ends with", _SENT_WITH.strip("_")))},
                # The two of them. An unreadable email is an unknown audience.
                audience=Audience(members=(email, ctx.email.lower())) if email else Audience(members=None),
            )
        if arg("thread"):
            link = _MESSAGE_LINK.search(require_named(arg("thread"), ctx.question, "Slack thread"))
            if not link:
                raise ActionRejected("Paste the Slack message's link (Copy link in Slack) to reply in its thread.")
            channel = _get(ctx.token, "conversations.info", {"channel": link["channel"]}).get("channel") or {}
            payload["thread_ts"] = link["root"] or f"{link['ts']}.{link['frac']}"
            where = f"#{channel.get('name') or channel.get('id')} (a thread)"
        else:
            channel = _channel_by_name(ctx.token, require_named(arg("channel"), ctx.question, "Slack channel"))
            where = f"#{channel.get('name') or channel['id']}"
        if not channel.get("is_member"):
            raise ActionRejected(f"You need to be a member of #{channel.get('name')} to post there.")
        return Proposal(
            action=self.name,
            target=ActionTarget("slack_channel", channel["id"], where),
            payload=payload,
            audience=channel_audience(ctx.token, channel),
        )

    def execute(self, key, target, payload, token) -> ActionResult:
        channel = target["external_id"]
        if target.get("kind") == "slack_user":
            # The DM is opened only now, at send: preparing a draft never writes.
            opened = _post(token, "conversations.open", {"users": channel},
                           missing=_NO_DM_PERSON if payload.get("as_person") else _NO_DM_BOT)
            channel = (opened.get("channel") or {}).get("id") or ""
        text = payload["text"] + (f"\n{payload['footer']}" if payload.get("footer") else "")
        message = {"channel": channel, "text": text, **_NO_UNFURL}
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
        if target.get("kind") == "slack_user" and not payload.get("as_person"):
            return ActionResult(ts, None)  # the bot's own DM with them: not one the asker can open
        try:
            link = _get(token, "chat.getPermalink",
                        {"channel": channel, "message_ts": ts}).get("permalink")
        except ActionError:
            link = None  # posted; only the link is missing
        return ActionResult(ts, link)
