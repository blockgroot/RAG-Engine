"""Act mode, Phase 3: Slack (docs/plans/2026-10-13-write-actions.md).

Pinned here:
- a message that asks, in plain words, for something to be done proposes a
  write only when the model's quote of the asking words is in the message and
  the surface is on; ``act`` forces it; anything else is answered exactly as
  before, and with Act off ``act`` is just a question;
- a DM drafts from what the PERSON can read; a channel drafts from that
  channel only, as the room, and shows the card only to the asker;
- the bot posts naming the asker; from a channel only into the same thread;
  elsewhere only into a channel the asker named, the bot is in and the asker
  belongs to (a list cut short is "can't check"); a public channel is public;
- a drafted ping or disguised link arrives as inert text;
- only the person who asked can approve, the tick travels with the click,
  and each Slack surface has its own admin switch;
- a person is found by a prior work address too.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.actions import ActionContext, Audience, Source, assess, policy, service
from app.actions import slack as slack_action
from app.actions.exposure import PUBLIC
from app.actions.store import ActionRow
from app.api import slack_act, slack_events
from app.config.settings import ActionsSettings
from app.core.exceptions import ActionConflict, ActionRejected, NotFoundError
from app.sources.slack_utils import channel_tag
from app.vectorstore.base import Viewer

from .conftest import requires_db


# --------------------------------------------------------------------------
# The explicit command
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text, request_text", [
    ("<@U0BOT> act file an issue in Platform", "file an issue in Platform"),
    ("act: comment on ENG-4", "comment on ENG-4"),
    ("ACT post in <#C123|eng> shipped &amp; done", "post in #eng shipped & done"),
    ("<@U0BOT> act", ""),
    ("<@U0BOT> agent file an issue in Platform", "file an issue in Platform"),  # the new name
    ("agents of change: who wrote that?", None),
    ("what is the action plan?", None),
    ("actually, who owns billing?", None),
    ("<@U0BOT> can you act on this?", None),
])
def test_only_the_act_command_is_a_write_request(text, request_text):
    assert slack_act.act_request(text) == request_text


class _Model:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def generate(self, prompt, **kw):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.mark.parametrize("reply, wanted", [
    ('{"act": true, "request_words": "create a Linear issue"}', True),
    ('{"act": true, "request_words": "open a ticket"}', False),  # quote not in the message
    ('{"act": true, "request_words": ""}', False),
    ('{"act": "true", "request_words": "create a Linear issue"}', False),
    ('{"act": false, "request_words": ""}', False),
    ("sure!", False),
    (RuntimeError("quota"), False),
])
def test_plain_words_count_only_with_a_quote_from_the_message(reply, wanted):
    text = "create a Linear issue in Platform about the scheduler flow"
    assert slack_act.asks_to_act(text, llm=_Model(reply)) is wanted


def test_plain_words_need_the_surface_switched_on(monkeypatch):
    model = _Model('{"act": true, "request_words": "create a Linear issue"}')
    on = {"slack_channel": False, "slack_dm": True}
    monkeypatch.setattr(slack_act.policy, "act_on", lambda org: True)
    monkeypatch.setattr(slack_act.policy, "surface_enabled", lambda org, kind: on[kind])
    text = "<@U0BOT> create a Linear issue in <#C1|eng> about this"
    assert slack_act.slack_request(text, org_id="o", is_dm=False, llm=model) is None
    assert model.prompts == []  # nobody pays for the check where Act is off
    assert slack_act.slack_request(text, org_id="o", is_dm=True, llm=model) == \
        "create a Linear issue in #eng about this"
    # `act` needs no check at all
    assert slack_act.slack_request("act file it", org_id="o", is_dm=False, llm=model) == "file it"
    assert len(model.prompts) == 1


def _handle_env(monkeypatch, *, acts_on=True):
    seen = {"answer": [], "act": []}
    monkeypatch.setattr(slack_act.policy, "act_on", lambda org_id: acts_on)
    monkeypatch.setattr(slack_events, "_org_for_team", lambda team: ("org-1", None))
    monkeypatch.setattr(slack_events, "get_live_connection_token", lambda *a, **k: "xoxb")
    monkeypatch.setattr(slack_events, "_slack_email", lambda t, u: "a@b.com")
    monkeypatch.setattr(slack_events, "get_member_by_address", lambda email, org_id: SimpleNamespace(
        id="u1", org_id="org-1", email="a@b.com"))
    monkeypatch.setattr(slack_events, "post_message", lambda *a, **k: None)
    monkeypatch.setattr(slack_events, "_scope_for_channel", lambda *a: None)
    monkeypatch.setattr(slack_events, "_dm_scopes", lambda org, uid: [(None, "Company")])
    monkeypatch.setattr(slack_events, "_answer",
                        lambda q, *a, **k: seen["answer"].append(q) or slack_events.SlackAnswer("ok"))
    monkeypatch.setattr(slack_act, "handle_act", lambda **kw: seen["act"].append(kw))
    monkeypatch.setattr(slack_act.policy, "surface_enabled", lambda org, kind: True)
    monkeypatch.setattr(slack_act, "asks_to_act", lambda text, llm=None: text.startswith("create"))
    return seen


def test_act_goes_to_act_and_nothing_else(monkeypatch):
    seen = _handle_env(monkeypatch)
    slack_events._handle({"type": "app_mention", "channel": "C1", "user": "U1",
                          "text": "<@U0BOT> act file an issue in Platform", "ts": "9.1"}, "T1")
    assert seen["answer"] == []
    [call] = seen["act"]
    assert call["request"] == "file an issue in Platform"
    assert call["is_dm"] is False and call["thread_ts"] == "9.1" and call["slack_user"] == "U1"


def test_plain_words_go_to_act(monkeypatch):
    seen = _handle_env(monkeypatch)
    slack_events._handle({"type": "app_mention", "channel": "C1", "user": "U1",
                          "text": "<@U0BOT> create a Linear issue in Platform", "ts": "9.1"}, "T1")
    assert seen["answer"] == []
    assert seen["act"][0]["request"] == "create a Linear issue in Platform"


def test_a_question_is_answered_as_before(monkeypatch):
    seen = _handle_env(monkeypatch)
    slack_events._handle({"type": "app_mention", "channel": "C1", "user": "U1",
                          "text": "<@U0BOT> what is blocking Platform?", "ts": "1"}, "T1")
    assert seen["act"] == [] and len(seen["answer"]) == 1


def test_with_act_off_the_command_is_just_a_question(monkeypatch):
    seen = _handle_env(monkeypatch, acts_on=False)
    slack_events._handle({"type": "message", "channel_type": "im", "channel": "D1",
                          "user": "U1", "text": "act file an issue", "ts": "1"}, "T1")
    assert seen["act"] == [] and len(seen["answer"]) == 1


# --------------------------------------------------------------------------
# Each place drafts from what it may read
# --------------------------------------------------------------------------


@pytest.fixture
def act_env(monkeypatch):
    seen = {"propose": [], "said": []}
    monkeypatch.setattr(slack_act, "_remember_slack_account", lambda *a: None)
    monkeypatch.setattr(slack_act.service, "propose",
                        lambda q, **kw: seen["propose"].append(kw) or service.ActOutcome("noted"))
    monkeypatch.setattr(slack_act, "_say", lambda token, **kw: seen["said"].append(kw))
    monkeypatch.setattr("app.api.actions._llm", lambda: None)
    monkeypatch.setattr("app.api.actions._vstore", lambda: None)
    monkeypatch.setattr("app.api.actions._embedder", lambda: None)
    monkeypatch.setattr("app.sources.google_groups.viewer_for_person",
                        lambda org, email: Viewer(email=email))
    return seen


USER = SimpleNamespace(id="u1", email="a@b.com")


def test_a_dm_drafts_as_the_person(act_env):
    slack_act.handle_act(request="file it", org_id="o", token="t",
                         user=USER, slack_user="U1", channel="D1", thread_ts=None, is_dm=True)
    [kw] = act_env["propose"]
    assert kw["viewer"] == Viewer(email="a@b.com") and kw["tags"] is None
    assert kw["surface"]["kind"] == "slack_dm"


def test_a_channel_drafts_from_that_channel_only_as_the_room(act_env, monkeypatch):
    monkeypatch.setattr(slack_events, "_scope_for_channel", lambda org, tag: None)
    slack_act.handle_act(request="file it", org_id="o", token="t",
                         user=USER, slack_user="U1", channel="C9", thread_ts="5.5", is_dm=False)
    [kw] = act_env["propose"]
    assert kw["viewer"] == Viewer(public_only=True, channels=("C9",))  # never the asker's access
    assert kw["tags"] == [channel_tag("C9")]
    assert kw["surface"] == {"kind": "slack_channel", "slack_user": "U1", "channel": "C9",
                             "thread_ts": "5.5"}
    assert act_env["said"][0]["is_dm"] is False  # shown to the asker only


def test_a_spaces_slack_bot_carries_act_too(monkeypatch):
    """A space's connection is the same bot in the same Slack: the post goes
    through it when there is no company-wide one."""
    monkeypatch.setattr(slack_action, "_bot_scope", lambda org: "space-1")
    monkeypatch.setattr(slack_action, "get_live_connection_token",
                        lambda org, provider, workspace_id=None: f"{provider}:{workspace_id}")
    assert slack_action.SlackPostMessage().token_for("o", "u", "slack_channel") == "slack:space-1"


def test_the_card_shown_in_a_channel_is_ephemeral(monkeypatch):
    posted = []
    monkeypatch.setattr(slack_act, "_post", lambda token, method, payload: posted.append(
        (method, payload)) or {"ok": True})
    slack_act._say("t", channel="C9", user="U1", thread_ts="5.5", is_dm=False, text="x")
    assert posted == [("chat.postEphemeral", {"channel": "C9", "user": "U1", "text": "x",
                                              "thread_ts": "5.5"})]


# --------------------------------------------------------------------------
# Posting to Slack (Slack's API faked)
# --------------------------------------------------------------------------


class FakeSlack:
    def __init__(self, channels, members, users=(), complete=True):
        self.channels, self.members, self.users, self.complete = channels, members, list(users), complete

    def get(self, token, method, params):
        if method == "conversations.info":
            return {"channel": next(c for c in self.channels if c["id"] == params["channel"])}
        raise AssertionError(method)

    def pages(self, token, method, params, key):
        if method == "conversations.list":
            return self.channels, True
        if method == "conversations.members":
            return self.members, self.complete
        if method == "users.list":
            return self.users, True
        raise AssertionError(method)


ENG = {"id": "C1", "name": "eng", "is_private": True, "is_member": True}
GENERAL = {"id": "C2", "name": "general", "is_private": False, "is_member": True}
SECRET = {"id": "C3", "name": "secret", "is_private": True, "is_member": False}


@pytest.fixture
def fake_slack(monkeypatch):
    def install(fake):
        monkeypatch.setattr(slack_action, "_get", fake.get)
        monkeypatch.setattr(slack_action, "_pages", fake.pages)
        return fake
    return install


def _sctx(question, surface):
    return ActionContext("org", None, "u1", "a@x.com", question, "xoxb", (), None, surface)


def test_from_a_channel_it_only_replies_in_that_thread(fake_slack):
    fake_slack(FakeSlack([ENG, GENERAL], ["U1"], users=[{"id": "U1", "profile": {"email": "a@x.com"}}]))
    here = {"kind": "slack_channel", "slack_user": "U1", "channel": "C1", "thread_ts": "7.7"}
    proposal = slack_action.SlackPostMessage().prepare({"text": "Filed it."}, _sctx("act post it", here))
    assert proposal.target.external_id == "C1" and proposal.payload["thread_ts"] == "7.7"
    with pytest.raises(ActionRejected, match="only reply in this thread"):
        slack_action.SlackPostMessage().prepare({"channel": "#general", "text": "x"},
                                                _sctx("act post in #general", here))


def test_elsewhere_the_channel_must_be_named_joined_and_yours(fake_slack):
    dm = {"kind": "slack_dm", "slack_user": "U1"}
    post = slack_action.SlackPostMessage()
    fake_slack(FakeSlack([ENG, SECRET], ["U1"], users=[{"id": "U1", "profile": {"email": "a@x.com"}}]))
    proposal = post.prepare({"channel": "#eng", "text": "Hi"}, _sctx("post in #eng: hi", dm))
    assert proposal.target.external_id == "C1" and proposal.payload["thread_ts"] is None
    with pytest.raises(ActionRejected, match="Name the Slack channel"):
        post.prepare({"channel": "#eng", "text": "Hi"}, _sctx("post the summary", dm))
    with pytest.raises(ActionRejected, match="Invite @Handbook"):
        post.prepare({"channel": "secret", "text": "Hi"}, _sctx("post in secret", dm))
    fake_slack(FakeSlack([ENG], ["U2"]))
    with pytest.raises(ActionRejected, match="member of #eng"):
        post.prepare({"channel": "eng", "text": "Hi"}, _sctx("post in eng", dm))
    fake_slack(FakeSlack([ENG], ["U1"], complete=False))
    with pytest.raises(ActionRejected, match="member of #eng"):
        post.prepare({"channel": "eng", "text": "Hi"}, _sctx("post in eng", dm))


def test_a_public_channel_never_gets_private_content(fake_slack):
    fake_slack(FakeSlack([GENERAL], ["U1"]))
    proposal = slack_action.SlackPostMessage().prepare(
        {"channel": "general", "text": "Reorg is on"}, _sctx("post in general", {"kind": "slack_dm", "slack_user": "U1"}))
    assert proposal.audience == Audience(public=True)
    secret = Source("d", "Reorg plan", public=False, viewers=("a@x.com",))
    assert assess([secret], proposal.audience, asker="a@x.com").verdict == PUBLIC


def test_a_private_channels_audience_is_its_people(fake_slack):
    fake_slack(FakeSlack([ENG], ["U1", "U2", "UBOT"], users=[
        {"id": "U1", "profile": {"email": "a@x.com"}},
        {"id": "U2", "profile": {"email": "b@x.com"}},
        {"id": "UBOT", "is_bot": True, "profile": {}}]))
    proposal = slack_action.SlackPostMessage().prepare(
        {"channel": "eng", "text": "x"}, _sctx("post in eng", {"kind": "slack_dm", "slack_user": "U1"}))
    assert proposal.audience == Audience(members=("a@x.com", "b@x.com"))


def test_a_drafted_ping_or_disguised_link_is_inert(fake_slack):
    fake_slack(FakeSlack([ENG], ["U1"], users=[{"id": "U1", "profile": {"email": "a@x.com"}}]))
    proposal = slack_action.SlackPostMessage().prepare(
        {"channel": "eng", "text": "<!channel> click <https://evil.example/?d=x|here>"},
        _sctx("post in eng", {"kind": "slack_dm", "slack_user": "U1"}))
    text = proposal.payload["text"]
    assert text.startswith("<@U1> asked Handbook to post this:\n")
    body = text.split("\n", 1)[1]
    assert "<!channel>" not in body and "&lt;!channel&gt;" in body
    assert "evil.example" not in body  # not in the asker's words, so removed


def test_inside_slack_the_bot_writes_and_from_the_web_the_person_does(monkeypatch):
    monkeypatch.setattr(slack_action, "_bot_scope", lambda org: None)
    monkeypatch.setattr(slack_action, "get_live_connection_token",
                        lambda org, provider, workspace_id=None: f"{provider}:{workspace_id}")
    monkeypatch.setattr("app.auth.user_connections.fresh_user_token",
                        lambda org, user, provider: f"own:{provider}:{user}")
    post = slack_action.SlackPostMessage()
    assert post.token_for("o", "u", "slack_dm") == "slack:None"
    assert post.token_for("o", "u", "slack_channel") == "slack:None"
    assert post.token_for("o", "u", "web") == "own:slack:u"


def test_from_the_web_it_posts_as_the_person_in_a_channel_they_named_and_are_in(fake_slack):
    web = {"kind": "web"}
    post = slack_action.SlackPostMessage()
    fake_slack(FakeSlack([ENG, GENERAL, SECRET], ["U1", "U2"], users=[
        {"id": "U1", "profile": {"email": "a@x.com"}}, {"id": "U2", "profile": {"email": "b@x.com"}}]))
    proposal = post.prepare({"channel": "#eng", "text": "Deploy is done"}, _sctx("post in #eng: deploy is done", web))
    assert proposal.payload == {"text": "Deploy is done", "thread_ts": None, "as_person": True}
    assert proposal.audience == Audience(members=("a@x.com", "b@x.com"))
    assert post.attribution_for(proposal.payload) == "as you"
    assert post.editable(proposal.payload) == {"body": "Deploy is done"}
    edited = post.edit(proposal.payload, {"body": "Deploy is done, please test"})
    assert edited["text"] == "Deploy is done, please test" and edited["as_person"] is True
    public = post.prepare({"channel": "general", "text": "x"}, _sctx("post in general", web))
    assert public.audience == Audience(public=True)
    with pytest.raises(ActionRejected, match="member of #secret"):
        post.prepare({"channel": "secret", "text": "Hi"}, _sctx("post in secret", web))
    with pytest.raises(ActionRejected, match="Name the Slack channel"):
        post.prepare({"channel": "#eng", "text": "Hi"}, _sctx("post the summary", web))


# --------------------------------------------------------------------------
# The click
# --------------------------------------------------------------------------


def _row(**over):
    base = dict(id=str(uuid.uuid4()), org_id="o", workspace_id=None, user_id="u1",
                conversation_id=None, surface="slack_channel", action="linear.create_issue",
                target={"name": "Platform (PLA)"}, payload={"title": "T", "body": "x" * 3500},
                payload_hash="h" * 64, sources=[{"title": "Reorg", "public": False}],
                exposure="wider", acknowledged=False, status="proposed", expires_at=None,
                decided_at=None, executed_at=None, result=None, created_at=None)
    return ActionRow(**{**base, **over})


def test_the_card_formats_the_draft_but_never_as_a_link_or_ping_and_asks_for_the_tick():
    row = _row()
    blocks = slack_act.card_blocks(row, audience_size=40, outsiders=3)
    texts = [b["text"] for b in blocks if b.get("type") == "section"]
    assert sum("x" * 100 in t["text"] for t in texts) == 2  # 3,500 chars over two sections
    body = "## Context\n- [ ] Document it\n<!channel> see <https://evil.example|here> [1]"
    shown = " ".join(b["text"]["text"] for b in slack_act.card_blocks(
        _row(payload={"title": "T", "body": body}), audience_size=None, outsiders=None)
        if b.get("type") == "section")
    assert "*Context*" in shown and "☐ Document it" in shown
    assert "<!channel>" not in shown and "&lt;!channel&gt;" in shown and "<https://" not in shown
    assert "[1]" in shown                                   # exactly what is sent
    assert any(b.get("block_id") == "act_ack" for b in blocks)
    approve = next(b for b in blocks if b.get("block_id") == "act_decide")["elements"][0]
    assert approve["value"] == f"{row.id}:{row.payload_hash}"
    assert not any(b.get("block_id") == "act_ack"
                   for b in slack_act.card_blocks(_row(exposure="none"), audience_size=None, outsiders=None))


@pytest.fixture
def click_env(monkeypatch):
    seen = {"confirm": [], "reject": [], "said": []}
    monkeypatch.setattr(slack_events, "_org_for_team", lambda team: ("o", None))
    monkeypatch.setattr(slack_act, "get_live_connection_token", lambda *a, **k: "xoxb")
    monkeypatch.setattr(slack_events, "_slack_email", lambda t, u: f"{u}@x.com")
    monkeypatch.setattr(slack_act, "get_member_by_address",
                        lambda email, org: SimpleNamespace(id=email.split("@")[0], email=email))
    monkeypatch.setattr(slack_act, "_respond", lambda url, text, replace=True: seen["said"].append((text, replace)))

    def confirm(**kw):
        seen["confirm"].append(kw)
        if kw["user_id"] != "U1":
            raise NotFoundError("no")
        return _row(status="succeeded", result={"external_id": "PLA-9", "url": "https://linear.app/x"})

    monkeypatch.setattr(slack_act.service, "confirm", confirm)
    monkeypatch.setattr(slack_act.store, "reject", lambda org, user, aid: seen["reject"].append(user) or True)
    monkeypatch.setattr(slack_act.store, "get", lambda org, user, aid: None)
    return seen


def _click(user, action_id, value, *, ticked=False):
    state = {"values": {"act_ack": {"act_ack": {"selected_options": [{"value": "ack"}] if ticked else []}}}}
    payload = {"type": "block_actions", "team": {"id": "T1"}, "user": {"id": user},
               "response_url": "https://hooks.slack.com/actions/x", "state": state}
    return payload, {"action_id": action_id, "value": value}


def test_approve_sends_the_shown_hash_and_the_tick(click_env):
    aid = str(uuid.uuid4())
    slack_act.decide(*_click("U1", "act_approve", f"{aid}:{'h' * 64}", ticked=True))
    [kw] = click_env["confirm"]
    assert kw["action_id"] == aid and kw["shown_hash"] == "h" * 64 and kw["acknowledged"] is True
    assert click_env["said"][-1][0].startswith("✅ Done: *PLA-9* · <https://linear.app/x|Open in Linear>")


def test_someone_else_cannot_approve(click_env):
    slack_act.decide(*_click("U2", "act_approve", f"{uuid.uuid4()}:{'h' * 64}"))
    assert click_env["said"] == [("Only the person who asked can approve this.", False)]


def test_a_missing_tick_keeps_the_card(click_env, monkeypatch):
    monkeypatch.setattr(slack_act.service, "confirm",
                        lambda **kw: (_ for _ in ()).throw(ActionConflict("Tick the acknowledgement to approve.")))
    slack_act.decide(*_click("U1", "act_approve", f"{uuid.uuid4()}:{'h' * 64}"))
    assert click_env["said"] == [("Tick the acknowledgement to approve.", False)]


def test_cancel_rejects_as_the_clicker(click_env):
    aid = str(uuid.uuid4())
    slack_act.decide(*_click("U1", "act_cancel", aid))
    assert click_env["reject"] == ["U1"] and click_env["said"][-1][0].startswith("Cancelled")


def test_the_click_route_checks_slacks_signature(monkeypatch):
    from app.api.main import create_app

    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shh")
    decided = []
    monkeypatch.setattr(slack_act, "decide", lambda payload, act: decided.append(act))
    client = TestClient(create_app())
    body = urlencode({"payload": json.dumps({
        "type": "block_actions", "actions": [{"action_id": "act_approve", "value": "x"}]})})
    assert client.post("/slack/interactivity", content=body, headers={
        "X-Slack-Request-Timestamp": str(int(time.time())), "X-Slack-Signature": "v0=bad",
        "Content-Type": "application/x-www-form-urlencoded"}).status_code == 401
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(b"shh", f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    ok = client.post("/slack/interactivity", content=body, headers={
        "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded"})
    assert ok.status_code == 200 and decided == [{"action_id": "act_approve", "value": "x"}]


# --------------------------------------------------------------------------
# Real database: surface switches, prior addresses
# --------------------------------------------------------------------------


@pytest.fixture
def org(store, org_cleanup, monkeypatch):
    from app.auth import create_admin

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    org_id = store.create_organization(f"Act3 {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"act3-{uuid.uuid4().hex[:8]}@example.com", org_id)
    policy.set_policy(org_id, policy.ACT_KEY, True)
    policy.set_policy(org_id, "linear.create_issue", True)
    return SimpleNamespace(id=org_id, admin=admin)


@requires_db
def test_each_slack_surface_has_its_own_switch(org):
    def ask(kind):
        return service.propose(
            "file an issue", org_id=org.id, user_id=org.admin.id, email=org.admin.email,
            viewer=Viewer(email=org.admin.email), llm=None, embedder=None, vstore=None,
            settings=ActionsSettings(), surface={"kind": kind, "slack_user": "U1"},
        ).reply

    assert "hasn't turned on Agent mode here" in ask("slack_dm")
    assert "hasn't turned on Agent mode here" in ask("slack_channel")
    policy.set_policy(org.id, policy.surface_key("slack_dm"), True)
    assert "hasn't turned on Agent mode here" not in ask("slack_dm")  # on to the next check
    assert "hasn't turned on Agent mode here" in ask("slack_channel")


@requires_db
def test_a_person_is_found_by_a_prior_work_address(org):
    from app.auth.users import get_member_by_address
    from app.db import get_connection

    old = f"old-{uuid.uuid4().hex[:6]}@example.com"
    with get_connection() as conn:
        conn.execute("INSERT INTO user_email_aliases (email, user_id) VALUES (%s, %s)",
                     (old, org.admin.id))
    assert get_member_by_address(old.upper(), org.id).id == org.admin.id
    assert get_member_by_address(org.admin.email, org.id).id == org.admin.id
    assert get_member_by_address(old, str(uuid.uuid4())) is None  # never across orgs


def test_a_dm_says_it_is_drafting_then_becomes_the_card(act_env, monkeypatch):
    posts = []
    monkeypatch.setattr(slack_act, "_post", lambda token, method, body: posts.append((method, body)) or {"ts": "9.9"})
    slack_act.handle_act(request="file it", org_id="o", token="t",
                         user=USER, slack_user="U1", channel="D1", thread_ts=None, is_dm=True)
    assert posts[0][0] == "chat.postMessage" and "Drafting" in posts[0][1]["text"]
    assert posts[1] == ("chat.update", {"channel": "D1", "ts": "9.9", "text": "noted"})
    assert act_env["said"] == []                              # replaced in place, not posted again


@pytest.mark.parametrize("action, result, payload, target, shown, tool", [
    ("linear.create_issue", {"external_id": "SYV-21"}, {"title": "T"}, {}, "SYV-21", "Linear"),
    ("slack.post_message", {"external_id": "1792146193.424979"}, {"text": "x"},
     {"name": "#rag-updates (this thread)"}, "#rag-updates (this thread)", "Slack"),
    ("drive.create_doc", {"external_id": "1AbCdEf"}, {"title": "Retro"}, {"name": "Your Google Drive"},
     "Retro", "Google Drive"),
])
def test_done_names_what_was_made_and_links_to_it(action, result, payload, target, shown, tool):
    row = _row(action=action, status="succeeded", payload=payload, target=target,
               result={**result, "url": "https://x.example/y"})
    assert slack_act._outcome_text(row) == f"✅ Done: *{shown}* · <https://x.example/y|Open in {tool}>"


def test_a_second_click_shows_the_outcome_with_its_link(click_env, monkeypatch):
    """A double click (or a click Slack delivered twice) used to replace Done
    with "already succeeded", losing the link."""
    from app.core.exceptions import ActionConflict

    def conflict(**kw):
        raise ActionConflict("This action was already succeeded.")

    monkeypatch.setattr(slack_act.service, "confirm", conflict)
    monkeypatch.setattr(slack_act.store, "get", lambda org, user, aid: _row(
        action="drive.create_doc", status="succeeded", payload={"title": "Retro"},
        result={"external_id": "1Ab", "url": "https://docs.google.com/d/1Ab"}))
    slack_act.decide(*_click("U1", "act_approve", f"{uuid.uuid4()}:{'h' * 64}"))
    assert click_env["said"][-1][0] == "✅ Done: *Retro* · <https://docs.google.com/d/1Ab|Open in Google Drive>"


def test_a_click_on_a_card_in_a_thread_answers_in_that_thread(click_env, monkeypatch):
    """Slack drops a response-URL reply to a private card in a thread at the
    bottom of the channel, so there the card goes and the outcome is posted
    privately in the thread itself."""
    raw, posted = [], []
    monkeypatch.setattr(slack_act, "_respond_raw", lambda url, body: raw.append(body))
    monkeypatch.setattr(slack_act, "_post", lambda token, method, body: posted.append((method, body)) or {})
    payload, act = _click("U1", "act_approve", f"{uuid.uuid4()}:{'h' * 64}")
    payload["container"] = {"type": "message", "is_ephemeral": True, "channel_id": "C9", "thread_ts": "5.5"}
    slack_act.decide(payload, act)
    assert raw == [{"delete_original": True}]
    [(method, body)] = posted
    assert method == "chat.postEphemeral" and body["thread_ts"] == "5.5" and body["user"] == "U1"
    assert body["text"].startswith("✅ Done:")
    assert click_env["said"] == []                  # never the channel-bottom reply


def test_a_typed_email_reads_as_typed_so_the_dm_request_is_found():
    """Slack sends <mailto:a@b|a@b>; with "mailto:" kept, the model's quote
    "DM a@b" was not in the text and the DM was answered as a question."""
    text = "DM <mailto:raj@syvora.com|raj@syvora.com> on Slack: fix is pushed"
    assert slack_act.plain_slack_text(text) == "DM raj@syvora.com on Slack: fix is pushed"

    class Model:
        def generate(self, prompt, max_tokens=None):
            return '{"act": true, "request_words": "DM raj@syvora.com on Slack"}'

    assert slack_act.asks_to_act(slack_act.plain_slack_text(text), llm=Model())


def test_asked_in_a_dm_the_bot_messages_the_person_named(fake_slack):
    """From Slack the bot posts, and it asked for a channel when the person
    wanted a DM. It DMs only someone the asker wrote, saying who asked."""
    raj = {"id": "U7", "name": "raj", "profile": {"email": "Raj@x.com", "real_name": "Raj D"}}
    fake_slack(FakeSlack([], [], users=[raj, {"id": "U1", "profile": {"email": "a@x.com"}}]))
    dm = {"kind": "slack_dm", "slack_user": "U1"}
    post = slack_action.SlackPostMessage()
    proposal = post.prepare({"person": "raj@x.com", "text": "Demo is live"},
                            _sctx("DM raj@x.com: Demo is live", dm))
    assert (proposal.target.kind, proposal.target.external_id) == ("slack_user", "U7")
    assert proposal.payload["text"].startswith("<@U1> asked Handbook to send you this:\n")
    assert proposal.audience == Audience(members=("raj@x.com", "a@x.com"))
    with pytest.raises(ActionRejected):                      # a person they never wrote
        post.prepare({"person": "raj@x.com", "text": "x"}, _sctx("DM someone: x", dm))
    here = {"kind": "slack_channel", "slack_user": "U1", "channel": "C1", "thread_ts": "7.7"}
    with pytest.raises(ActionRejected, match="DM me"):       # never from a channel
        post.prepare({"person": "raj@x.com", "text": "x"}, _sctx("DM raj@x.com: x", here))


@pytest.mark.parametrize("as_person, fix", [(True, "Reconnect Slack"), (False, "im:write bot scope")])
def test_a_missing_dm_scope_names_the_right_fix(monkeypatch, as_person, fix):
    """From Slack the bot sends, so "reconnect your Slack" sent people the wrong way."""
    class Refused:
        def json(self):
            return {"ok": False, "error": "missing_scope"}

    monkeypatch.setattr(slack_action.httpx, "post", lambda *a, **k: Refused())
    payload = {"text": "hi", "thread_ts": None, **({"as_person": True} if as_person else {})}
    with pytest.raises(slack_action.ActionError, match=fix):
        slack_action.SlackPostMessage().execute(
            "k", {"kind": "slack_user", "external_id": "U7"}, payload, "tok")
