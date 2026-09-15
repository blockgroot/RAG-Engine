"""Ask-in-Slack: signature auth, and the channel-vs-DM scoping decision.

The scoping test is the one that matters. Answering an in-channel question
from Drive/Notion/another channel would broadcast content some people in the
room may not be able to open, so "a channel question is filtered to that
channel" is a permission guarantee, not a preference.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.api import slack_events
from app.sources.slack_utils import channel_tag

SECRET = "test-slack-signing-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    from app.api.main import create_app

    return TestClient(create_app())


def _signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(
        SECRET.encode(), b"v0:" + ts.encode() + b":" + raw, hashlib.sha256
    ).hexdigest()
    return raw, {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def test_unset_secret_closes_the_route(monkeypatch):
    """An unconfigured secret 404s rather than accepting unverified posts."""
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    from app.api.main import create_app

    response = TestClient(create_app()).post("/slack/events", json={"type": "x"})
    assert response.status_code == 404


def test_rejects_a_bad_signature(client):
    response = client.post(
        "/slack/events",
        content=json.dumps({"type": "event_callback"}).encode(),
        headers={
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=deadbeef",
        },
    )
    assert response.status_code == 401


def test_rejects_a_replayed_old_signature(client):
    """A captured request must not stay valid forever."""
    raw = json.dumps({"type": "event_callback"}).encode()
    old = str(int(time.time()) - 60 * 60)
    sig = "v0=" + hmac.new(
        SECRET.encode(), b"v0:" + old.encode() + b":" + raw, hashlib.sha256
    ).hexdigest()
    response = client.post(
        "/slack/events",
        content=raw,
        headers={"X-Slack-Request-Timestamp": old, "X-Slack-Signature": sig},
    )
    assert response.status_code == 401


def test_answers_the_url_verification_handshake(client):
    raw, headers = _signed({"type": "url_verification", "challenge": "abc123"})
    response = client.post("/slack/events", content=raw, headers=headers)
    assert response.status_code == 200
    assert response.json()["challenge"] == "abc123"


def test_ignores_its_own_messages(client, monkeypatch):
    """The bot's reply is itself a message event -- reacting would loop."""
    called = []
    monkeypatch.setattr(slack_events, "_handle", lambda *a: called.append(a))
    raw, headers = _signed(
        {
            "type": "event_callback",
            "team_id": "T1",
            "event": {"type": "message", "bot_id": "B1", "text": "hi", "channel": "C1"},
        }
    )
    assert client.post("/slack/events", content=raw, headers=headers).status_code == 200
    assert called == []


def _capture_scope(monkeypatch) -> list:
    """Run ``_handle`` with every external dependency faked; record the scope."""
    seen: list = []
    monkeypatch.setattr(slack_events, "_org_for_team", lambda team: ("org-1", None))
    monkeypatch.setattr(slack_events, "get_live_connection_token", lambda *a, **k: "tok")
    monkeypatch.setattr(slack_events, "_slack_email", lambda t, u: "a@b.com")
    monkeypatch.setattr(
        slack_events,
        "get_user_by_email",
        lambda email: type("U", (), {"id": "u1", "org_id": "org-1"})(),
    )
    monkeypatch.setattr(slack_events, "post_message", lambda *a, **k: None)
    monkeypatch.setattr(slack_events, "_channel_is_indexed", lambda *a: True)

    def _fake_answer(question, org_id, workspace_id, tags):
        seen.append(tags)
        return "answer"

    monkeypatch.setattr(slack_events, "_answer", _fake_answer)
    return seen


def test_channel_question_is_filtered_to_that_channel(monkeypatch):
    seen = _capture_scope(monkeypatch)
    slack_events._handle(
        {"type": "app_mention", "channel": "C123", "user": "U1", "text": "what?", "ts": "1"},
        "T1",
    )
    assert seen == [[channel_tag("C123")]]


def test_dm_searches_everything(monkeypatch):
    """A DM is private and one-to-one, so it matches the web app's scope."""
    seen = _capture_scope(monkeypatch)
    slack_events._handle(
        {
            "type": "message",
            "channel_type": "im",
            "channel": "D123",
            "user": "U1",
            "text": "what?",
            "ts": "1",
        },
        "T1",
    )
    assert seen == [None]


def test_a_person_without_an_account_is_refused(monkeypatch):
    """Never answer org-scoped content without resolving who is asking."""
    posted: list = []
    _capture_scope(monkeypatch)
    monkeypatch.setattr(slack_events, "get_user_by_email", lambda email: None)
    monkeypatch.setattr(
        slack_events, "post_message", lambda tok, ch, text, ts=None: posted.append(text)
    )
    slack_events._handle(
        {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1",
         "text": "q", "ts": "1"},
        "T1",
    )
    assert posted == [slack_events._NO_ACCOUNT]


class _Resp:
    def __init__(self, answer, chart=None):
        self.answer = answer
        self.chart = chart


def test_chart_numbers_are_printed_not_just_the_caption():
    """Slack has no canvas: a caption with no data under it reads as a non-answer."""
    text = slack_events._with_chart_values(
        _Resp(
            "Commits by author, last quarter",
            {"points": [
                {"bucket": "2026-07-01", "group": "18-sana", "series": None, "value": 12},
                {"bucket": "2026-07-01", "group": "ada", "series": None, "value": 4},
            ]},
        )
    )
    assert "Commits by author" in text
    assert "18-sana: 12" in text
    assert "ada: 4" in text


def test_a_plain_answer_is_left_alone():
    assert slack_events._with_chart_values(_Resp("25 days.")) == "25 days."


def test_an_empty_chart_falls_back_to_its_caption():
    """`points: []` means the chart ran with nothing to show -- the caption says which."""
    caption = "Nothing recorded from GitHub in this window."
    assert slack_events._with_chart_values(_Resp(caption, {"points": []})) == caption


def test_an_unconnected_channel_says_so_instead_of_refusing(monkeypatch):
    """A hedged "I couldn't find that" tells nobody the channel was never indexed."""
    posted: list = []
    _capture_scope(monkeypatch)
    monkeypatch.setattr(slack_events, "_channel_is_indexed", lambda *a: False)
    monkeypatch.setattr(
        slack_events, "post_message", lambda tok, ch, text, ts=None: posted.append(text)
    )

    def _must_not_run(*a, **k):
        raise AssertionError("an unconnected channel must not reach the pipeline")

    monkeypatch.setattr(slack_events, "_answer", _must_not_run)
    slack_events._handle(
        {"type": "app_mention", "channel": "C999", "user": "U1", "text": "q", "ts": "1"},
        "T1",
    )
    assert posted == [slack_events._NOT_CONNECTED]


def test_a_dm_never_checks_channel_indexing(monkeypatch):
    """A DM has no channel to be connected -- the check must not gate it."""
    seen = _capture_scope(monkeypatch)
    monkeypatch.setattr(
        slack_events, "_channel_is_indexed", lambda *a: (_ for _ in ()).throw(
            AssertionError("a DM must not check channel indexing")
        )
    )
    slack_events._handle(
        {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1",
         "text": "q", "ts": "1"},
        "T1",
    )
    assert seen == [None]


def test_a_retry_delivery_is_dropped(client, monkeypatch):
    """Slack retries on a missed 3s ack; the first delivery is already answering."""
    called = []
    monkeypatch.setattr(slack_events, "_handle", lambda *a: called.append(a))
    raw, headers = _signed(
        {
            "type": "event_callback",
            "team_id": "T1",
            "event": {"type": "app_mention", "channel": "C1", "user": "U1",
                      "text": "q", "ts": "1"},
        }
    )
    first = client.post("/slack/events", content=raw, headers=headers)
    assert first.status_code == 200
    assert len(called) == 1

    retry = client.post(
        "/slack/events", content=raw, headers={**headers, "X-Slack-Retry-Num": "1"}
    )
    assert retry.status_code == 200
    assert len(called) == 1, "a retry must not queue a second answer"


def _capture_post(monkeypatch) -> list:
    """Like ``_capture_scope`` but records where the reply was posted."""
    posted: list = []
    monkeypatch.setattr(slack_events, "_org_for_team", lambda team: ("org-1", None))
    monkeypatch.setattr(slack_events, "get_live_connection_token", lambda *a, **k: "tok")
    monkeypatch.setattr(slack_events, "_slack_email", lambda t, u: "a@b.com")
    monkeypatch.setattr(
        slack_events, "get_user_by_email",
        lambda email: type("U", (), {"id": "u1", "org_id": "org-1"})(),
    )
    monkeypatch.setattr(slack_events, "_channel_is_indexed", lambda *a: True)
    monkeypatch.setattr(slack_events, "_answer", lambda *a: "the answer")
    monkeypatch.setattr(
        slack_events, "post_message",
        lambda tok, ch, text, ts=None: posted.append((ch, text, ts)),
    )
    return posted


def test_a_top_level_question_is_answered_in_the_channel(monkeypatch):
    """Not threaded: a "1 reply" link is a second place to look for one answer."""
    posted = _capture_post(monkeypatch)
    slack_events._handle(
        {"type": "app_mention", "channel": "C1", "user": "U1", "text": "q", "ts": "111"},
        "T1",
    )
    assert posted == [("C1", "the answer", None)]


def test_a_question_inside_a_thread_is_answered_in_that_thread(monkeypatch):
    """The conversation already lives there; answering outside would split it."""
    posted = _capture_post(monkeypatch)
    slack_events._handle(
        {"type": "app_mention", "channel": "C1", "user": "U1", "text": "q",
         "ts": "222", "thread_ts": "111"},
        "T1",
    )
    assert posted == [("C1", "the answer", "111")]
