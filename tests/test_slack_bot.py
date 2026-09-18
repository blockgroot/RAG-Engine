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
    monkeypatch.setattr(slack_events, "_scope_for_channel", lambda *a: None)
    monkeypatch.setattr(slack_events, "_dm_scopes", lambda org, uid: [(None, "Company")])

    def _fake_answer(question, org_id, workspace_id, tags, scope_label=None, **_):
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
    assert "12.0" not in text


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
    monkeypatch.setattr(slack_events, "_scope_for_channel", lambda *a: slack_events._NO_SCOPE)
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
        slack_events, "_scope_for_channel", lambda *a: (_ for _ in ()).throw(
            AssertionError("a DM must not resolve a channel scope")
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


def _capture_post(monkeypatch, *, update_ok: bool = True) -> tuple[list, list]:
    """Record posts AND edits.

    The bot posts a placeholder first and edits it into the answer, so a test
    that only records posts cannot tell "answered in the channel" from
    "answered twice".
    """
    posted: list = []
    edited: list = []
    monkeypatch.setattr(slack_events, "_org_for_team", lambda team: ("org-1", None))
    monkeypatch.setattr(slack_events, "get_live_connection_token", lambda *a, **k: "tok")
    monkeypatch.setattr(slack_events, "_slack_email", lambda t, u: "a@b.com")
    monkeypatch.setattr(
        slack_events, "get_user_by_email",
        lambda email: type("U", (), {"id": "u1", "org_id": "org-1"})(),
    )
    monkeypatch.setattr(slack_events, "_scope_for_channel", lambda *a: None)
    monkeypatch.setattr(slack_events, "_answer", lambda *a, **k: "the answer")

    def _post(tok, ch, text, ts=None):
        posted.append((ch, text, ts))
        return "msg-1"

    def _update(tok, ch, ts, text):
        edited.append((ch, ts, text))
        return update_ok

    monkeypatch.setattr(slack_events, "post_message", _post)
    monkeypatch.setattr(slack_events, "update_message", _update)
    return posted, edited


def test_a_top_level_question_is_answered_in_a_thread_under_it(monkeypatch):
    """Threaded under the question, not posted into the channel.

    A grounded answer is long; several of them dropped straight into a channel
    bury whatever else people were discussing. The thread keeps the answer
    attached to the question that earned it.
    """
    posted, edited = _capture_post(monkeypatch)
    slack_events._handle(
        {"type": "app_mention", "channel": "C1", "user": "U1", "text": "q", "ts": "111"},
        "T1",
    )
    # The question's own `ts` becomes the thread the placeholder is posted in,
    # and the answer replaces that placeholder -- so the answer lands in the
    # thread, never in the channel.
    assert posted == [("C1", slack_events._SEARCHING, "111")]
    assert edited == [("C1", "msg-1", "the answer")]


def test_a_question_inside_a_thread_is_answered_in_that_thread(monkeypatch):
    """The conversation already lives there; answering outside would split it."""
    posted, edited = _capture_post(monkeypatch)
    slack_events._handle(
        {"type": "app_mention", "channel": "C1", "user": "U1", "text": "q",
         "ts": "222", "thread_ts": "111"},
        "T1",
    )
    # The PLACEHOLDER carries the thread, because it is the message that
    # becomes the answer -- posting it outside the thread would move the reply.
    assert posted == [("C1", slack_events._SEARCHING, "111")]
    assert edited == [("C1", "msg-1", "the answer")]


def test_the_answer_still_arrives_when_the_edit_fails(monkeypatch):
    """Leaving "Searching…" up and dropping a grounded answer is the worst of
    both outcomes, so a failed edit falls back to posting."""
    posted, edited = _capture_post(monkeypatch, update_ok=False)
    slack_events._handle(
        {"type": "app_mention", "channel": "C1", "user": "U1", "text": "q", "ts": "111"},
        "T1",
    )
    assert edited, "an edit was never attempted"
    # The fallback post must land in the SAME thread as the placeholder it is
    # replacing, or a failed edit moves the answer out into the channel.
    assert posted[-1] == ("C1", "the answer", "111")


def _capture_answer_scope(monkeypatch) -> list:
    """Record the (workspace_id, tags) the answer was actually run with."""
    seen: list = []
    monkeypatch.setattr(slack_events, "_org_for_team", lambda team: ("org-1", "space-1"))
    monkeypatch.setattr(slack_events, "get_live_connection_token", lambda *a, **k: "tok")
    monkeypatch.setattr(slack_events, "_slack_email", lambda t, u: "a@b.com")
    monkeypatch.setattr(
        slack_events, "get_user_by_email",
        lambda email: type("U", (), {"id": "u1", "org_id": "org-1"})(),
    )
    monkeypatch.setattr(slack_events, "post_message", lambda *a, **k: None)
    # Default to "no name in the question" so each test exercises the path it
    # names; the named-scope tests override this.
    monkeypatch.setattr(
        slack_events, "choose_scope", lambda org, scopes, q: slack_events._NO_MATCH
    )
    monkeypatch.setattr(
        slack_events, "_answer",
        lambda q, org, ws, tags, label=None, **_: seen.append((ws, tags, label))
        or "answer",
    )
    return seen


def test_a_dm_defaults_to_company_when_the_asker_is_in_no_space(monkeypatch):
    """The token's scope must never decide what the answer may read."""
    seen = _capture_answer_scope(monkeypatch)
    monkeypatch.setattr(slack_events, "_dm_scopes", lambda org, uid: [(None, "Company")])
    slack_events._handle(
        {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1",
         "text": "what is the leave policy?", "ts": "1"},
        "T1",
    )
    # Space-scoped token, but the answer is org-wide and labelled Company.
    assert seen == [(None, None, "Company")]


def test_a_channel_is_answered_from_the_scope_that_indexes_it(monkeypatch):
    """A private channel connected to ONE space stays answerable in that channel.

    Without this, making the bot work in a private channel would require
    indexing it org-wide -- publishing it to the whole company.
    """
    seen = _capture_answer_scope(monkeypatch)
    monkeypatch.setattr(slack_events, "_scope_for_channel", lambda org, tag: "space-1")
    slack_events._handle(
        {"type": "app_mention", "channel": "C_PRIVATE", "user": "U1",
         "text": "what was decided?", "ts": "1"},
        "T1",
    )
    assert seen == [("space-1", [channel_tag("C_PRIVATE")], None)]


def test_an_org_wide_channel_is_answered_org_wide(monkeypatch):
    seen = _capture_answer_scope(monkeypatch)
    monkeypatch.setattr(slack_events, "_scope_for_channel", lambda org, tag: None)
    slack_events._handle(
        {"type": "app_mention", "channel": "C_PUBLIC", "user": "U1",
         "text": "q", "ts": "1"},
        "T1",
    )
    assert seen == [(None, [channel_tag("C_PUBLIC")], None)]


def test_not_found_is_distinguishable_from_org_wide():
    """`None` is a valid scope, so the sentinel cannot be `None`."""
    assert slack_events._NO_SCOPE is not None


def test_a_dm_can_reach_a_space_the_asker_belongs_to(monkeypatch):
    """A DM is the PERSON's surface: they see org-wide AND their own spaces."""
    seen = _capture_answer_scope(monkeypatch)
    monkeypatch.setattr(
        slack_events, "_dm_scopes",
        lambda org, uid: [(None, "Company"), ("ws-meet", "Meeting notes")],
    )
    monkeypatch.setattr(slack_events, "choose_scope", lambda org, scopes, q: "ws-meet")
    slack_events._handle(
        {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1",
         "text": "what did we agree on Tuesday?", "ts": "1"},
        "T1",
    )
    assert seen == [("ws-meet", None, "Meeting notes")]


def test_only_the_askers_own_scopes_are_ever_probed(monkeypatch):
    """Membership is READ, never inferred -- the probe is offered nothing else."""
    offered: list = []
    _capture_answer_scope(monkeypatch)
    monkeypatch.setattr(
        slack_events, "_dm_scopes",
        lambda org, uid: [(None, "Company"), ("ws-mine", "Mine")],
    )
    monkeypatch.setattr(
        slack_events, "choose_scope",
        lambda org, scopes, q: offered.append([sid for sid, _ in scopes]) or None,
    )
    slack_events._handle(
        {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1",
         "text": "q", "ts": "1"},
        "T1",
    )
    assert offered == [[None, "ws-mine"]]
    assert "ws-someone-else" not in offered[0]


def test_an_unmatched_probe_falls_back_to_company(monkeypatch):
    """Nothing indexed anywhere must not strand the question in a space."""
    seen = _capture_answer_scope(monkeypatch)
    monkeypatch.setattr(
        slack_events, "_dm_scopes",
        lambda org, uid: [(None, "Company"), ("ws-x", "X")],
    )
    monkeypatch.setattr(
        slack_events, "choose_scope", lambda org, scopes, q: slack_events._NO_MATCH
    )
    slack_events._handle(
        {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1",
         "text": "q", "ts": "1"},
        "T1",
    )
    assert seen == [(None, None, "Company")]


def test_the_reply_names_the_source_and_the_scope(monkeypatch):
    """"Where did this come from?" must be answerable from the message itself."""
    monkeypatch.setattr(
        slack_events, "choose_agent",
        lambda q, org, workspace_id=None: type(
            "D", (), {"agent_key": "google", "chart_spec": None, "chart_refusal": None}
        )(),
    )

    class _G:
        def invoke(self, state):
            return {"response": type("R", (), {"answer": "Tuesday's notes.", "chart": None})()}

    monkeypatch.setattr("app.agent.orchestration.build_agent_graph", lambda g: _G())
    text = slack_events._answer("q", "org-1", "ws-meet", None, "Meeting notes")
    assert text.startswith("_Google Drive · Meeting notes_\n")
    assert "Tuesday's notes." in text


def test_markdown_becomes_slack_mrkdwn():
    """Slack renders none of Markdown: asterisks and hyphens show literally."""
    out = slack_events._to_slack_mrkdwn(
        "### Key details\n- **Eligibility**: full-time only\n- Amount: Rs. 5,000\n\nPlain line."
    )
    assert "*Key details*" in out and "###" not in out
    assert "•   *Eligibility*: full-time only" in out
    assert "**" not in out
    assert "Plain line." in out


def test_a_numbered_list_and_plain_text_are_left_alone():
    text = "1. First\n2. Second\n\nJust a sentence."
    assert slack_events._to_slack_mrkdwn(text) == text


def test_words_tolerates_a_retyped_title():
    """"Meeting_note_1" must match "Meeting_notes_1" -- people retype from memory."""
    from app.agent import routing
    assert routing._words("Meeting_notes_1") == routing._words("Meeting_note_1")


def test_a_named_space_beats_the_probe(monkeypatch):
    seen = _capture_answer_scope(monkeypatch)
    monkeypatch.setattr(
        slack_events, "_dm_scopes",
        lambda org, uid: [(None, "Company"), ("ws-meet", "Meeting notes")],
    )
    monkeypatch.setattr(slack_events, "choose_scope", lambda org, scopes, q: "ws-meet")

    slack_events._handle(
        {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1",
         "text": "what should I know from Meeting_note_1?", "ts": "1"},
        "T1",
    )
    assert seen == [("ws-meet", None, "Meeting notes")]


def test_an_ambiguous_name_falls_through_to_the_probe(monkeypatch):
    """Two scopes matching resolves to NEITHER: the wrong space beats no space."""
    seen = _capture_answer_scope(monkeypatch)
    monkeypatch.setattr(
        slack_events, "_dm_scopes",
        lambda org, uid: [(None, "Company"), ("ws-a", "A"), ("ws-b", "B")],
    )
    monkeypatch.setattr(
        slack_events, "choose_scope", lambda org, scopes, q: slack_events._NO_MATCH
    )
    monkeypatch.setattr(slack_events, "choose_scope", lambda org, scopes, q: "ws-b")
    slack_events._handle(
        {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1",
         "text": "q", "ts": "1"},
        "T1",
    )
    assert seen == [("ws-b", None, "B")]


def test_counts_never_render_with_a_decimal_point():
    """SQL returns floats; "2.0 issues" is not a more precise count, just wrong."""
    text = slack_events._with_chart_values(
        _Resp("Where the work sits by state",
              {"points": [{"bucket": "b", "group": "Backlog", "value": 2.0},
                          {"bucket": "b", "group": "Done", "value": 3.0}]})
    )
    assert "Backlog: 2" in text and "2.0" not in text
    assert "Done: 3" in text and "3.0" not in text


def test_a_genuinely_fractional_value_keeps_its_decimals():
    text = slack_events._with_chart_values(
        _Resp("Average", {"points": [{"bucket": "b", "group": "Mean", "value": 2.5}]})
    )
    assert "Mean: 2.5" in text
