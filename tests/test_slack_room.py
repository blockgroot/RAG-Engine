"""A Slack channel reply reads what EVERY member can open (`security.room`).

The room is the reply's audience, so a private document is used only when each
member's own ACL opens it; one unknown member leaves the room public-plus-channel.
"""

from __future__ import annotations

from app.security import room
from app.vectorstore.base import Viewer


def _people(monkeypatch, acls: dict[str, list[str]]):
    import app.sources.google_groups as groups

    class _V:
        def __init__(self, email):
            self.email = email

        def acl(self):
            return acls[self.email]

    monkeypatch.setattr(groups, "viewer_for_person", lambda org, email: _V(email))
    seen = {}

    def _docs(org, member_acls, shared):
        seen["acls"], seen["shared"] = member_acls, shared
        return ["doc:7"], True

    monkeypatch.setattr("app.security.visibility.room_documents", _docs)
    return seen


def test_the_room_holds_what_every_member_shares_plus_their_common_documents(monkeypatch):
    seen = _people(monkeypatch, {
        "ada@acme.com": ["ada@acme.com", "domain:acme.com", "group:eng@acme.com"],
        "bo@acme.com": ["bo@acme.com", "domain:acme.com"],
    })
    viewer = room.room_viewer("o", "C1", ["ada@acme.com", "bo@acme.com"])
    assert viewer.public_only and viewer.channels == ("C1",)
    assert viewer.room == ("domain:acme.com", "doc:7")  # never one person's own entry
    assert viewer.room_members == ("ada@acme.com", "bo@acme.com")
    assert seen["shared"] == ["domain:acme.com"] and len(seen["acls"]) == 2
    assert viewer.acl() == ["channel:c1", "domain:acme.com", "doc:7"]


def test_an_unknown_room_reads_nothing_private_and_says_so():
    viewer = room.room_viewer("o", "C1", None)
    assert viewer.acl() == ["channel:c1"] and viewer.room_note == room.UNKNOWN_ROOM


def test_one_member_without_access_means_no_document():
    """`room_documents` binds one overlap per member: an empty ACL matches nothing."""
    from app.security.visibility import room_documents

    assert room_documents("o", [["ada@acme.com"], []], []) == ([], True)


def test_the_room_is_reused_and_an_unknown_one_is_not(monkeypatch):
    _people(monkeypatch, {"ada@acme.com": ["ada@acme.com"]})
    room._cache.clear()
    calls = []
    read = lambda: calls.append(1) or ["ada@acme.com"]  # noqa: E731
    room.cached_room_viewer("o", "C1", read)
    room.cached_room_viewer("o", "C1", read)
    assert calls == [1]
    unknown = []
    room.cached_room_viewer("o", "C2", lambda: unknown.append(1))
    room.cached_room_viewer("o", "C2", lambda: unknown.append(1))
    assert unknown == [1, 1]
    assert isinstance(room.cached_room_viewer("o", "C1", read), Viewer)


def _slack(monkeypatch, replies: dict):
    from app.sources import slack_utils

    def _get(token, method, params, **kw):
        key = method if method != "users.info" else f"users.info:{params['user']}"
        value = replies[key]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(slack_utils, "_get", _get)
    slack_utils._emails.clear()
    return slack_utils


def test_every_human_member_is_read_and_one_unreadable_makes_the_room_unknown(monkeypatch):
    replies = {
        "conversations.members": {"members": ["U1", "U2", "B1"]},
        "users.info:U1": {"user": {"profile": {"email": "Ada@Acme.com"}}},
        "users.info:U2": {"user": {"profile": {"email": "bo@acme.com"}}},
        "users.info:B1": {"user": {"is_bot": True}},
    }
    slack = _slack(monkeypatch, replies)
    assert slack.channel_member_emails("t", "C1") == ["ada@acme.com", "bo@acme.com"]
    replies["users.info:U2"] = {"user": {"profile": {}}}  # email hidden
    slack._emails.clear()
    assert slack.channel_member_emails("t", "C1") is None


def test_a_room_too_large_to_check_is_unknown(monkeypatch):
    slack = _slack(monkeypatch, {"conversations.members": {
        "members": [f"U{i}" for i in range(101)]}})
    assert slack.channel_member_emails("t", "C1") is None


def test_the_conversation_is_read_oldest_first_without_the_bot(monkeypatch):
    slack = _slack(monkeypatch, {"conversations.history": {"messages": [
        {"user": "U2", "text": "agreed, friday"},
        {"bot_id": "B1", "text": "Searching..."},
        {"user": "U1", "text": "ship LIN-12 on friday?"},
    ], "has_more": True}})
    text, cut = slack.recent_conversation("t", "C1", None, before_ts="9")
    assert text == "<@U1>: ship LIN-12 on friday?\n<@U2>: agreed, friday" and cut


def test_a_members_email_is_read_once_then_remembered(monkeypatch):
    calls = []
    replies = {
        "conversations.members": {"members": ["U1", "B1"]},
        "users.info:U1": {"user": {"profile": {"email": "ada@acme.com"}}},
        "users.info:B1": {"user": {"is_bot": True}},
    }
    slack = _slack(monkeypatch, replies)
    slack._emails.clear()
    inner = slack._get
    monkeypatch.setattr(slack, "_get", lambda t, m, p, **k: calls.append(m) or inner(t, m, p))
    assert slack.channel_member_emails("t", "C1") == ["ada@acme.com"]
    assert slack.channel_member_emails("t", "C1") == ["ada@acme.com"]
    assert calls.count("users.info") == 2  # once per member, bot included, never again


def test_an_unreadable_member_is_not_remembered(monkeypatch):
    from app.core.exceptions import SourceError

    replies = {"conversations.members": {"members": ["U9"]},
               "users.info:U9": SourceError("slack down")}
    slack = _slack(monkeypatch, replies)
    slack._emails.clear()
    assert slack.channel_member_emails("t", "C1") is None
    replies["users.info:U9"] = {"user": {"profile": {"email": "z@acme.com"}}}
    assert slack.channel_member_emails("t", "C1") == ["z@acme.com"]


def test_a_room_chart_says_under_its_caption_that_items_were_left_out():
    """Slack shows the caption, not the caveat line, so the note rides there."""
    from app.agent.insights_agent import _caption

    spec = type("S", (), {"group_by": None})()
    points = [{"bucket": "2026-11", "value": 3}]
    note = "Some items aren't open to everyone in this channel, so they're not counted here."
    assert _caption(spec, {"title": "Issues closed", "room_cut": note}, points) == (
        f"Issues closed. {note}")
    assert _caption(spec, {"title": "Issues closed", "room_cut": None}, points) == "Issues closed"
