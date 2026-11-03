"""A Slack question takes the same person + company limits as the web app."""

from __future__ import annotations

from types import SimpleNamespace

from app.api import slack_events
from app.security import rate_limit


def _slack(monkeypatch) -> tuple[list, list]:
    posted, answered = [], []
    monkeypatch.setattr(slack_events, "_org_for_team", lambda team: ("org-1", None))
    monkeypatch.setattr(slack_events, "get_live_connection_token", lambda *a, **k: "tok")
    monkeypatch.setattr(slack_events, "_slack_email", lambda t, u: "a@b.com")
    monkeypatch.setattr(
        slack_events,
        "get_member_by_address",
        lambda email, org_id: SimpleNamespace(id="user-1", email=email),
    )
    monkeypatch.setattr(slack_events, "post_message", lambda token, ch, text, ts=None: posted.append(text))
    monkeypatch.setattr(slack_events, "_scope_for_channel", lambda *a: None)
    monkeypatch.setattr(slack_events, "_dm_scopes", lambda org, uid: [(None, "Company")])
    monkeypatch.setattr(rate_limit, "_member_count", lambda org_id: 1)

    def _answer(*a, **k):
        answered.append(a)
        return slack_events.SlackAnswer("the answer")

    monkeypatch.setattr(slack_events, "_answer", _answer)
    monkeypatch.setattr(slack_events, "_deliver", lambda *a, **k: None)
    return posted, answered


def _ask(n: int) -> None:
    slack_events._handle(
        {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1",
         "text": f"question {n}", "ts": f"1.{n}"},
        "T1",
    )


def test_over_the_limit_a_slack_question_is_told_when_to_try_again(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_QUESTION_BURST", "2")
    monkeypatch.setenv("RATE_LIMIT_QUESTIONS_PER_MINUTE", "1")
    posted, answered = _slack(monkeypatch)

    for n in range(3):
        _ask(n)

    assert len(answered) == 2
    assert posted[-1].startswith("Too many requests. Please try again in ")


def test_slack_and_the_web_share_one_allowance(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_QUESTION_BURST", "1")
    monkeypatch.setenv("RATE_LIMIT_QUESTIONS_PER_MINUTE", "1")
    posted, answered = _slack(monkeypatch)

    assert rate_limit.question_decision("user-1", "org-1").allowed  # asked on the web
    _ask(1)

    assert answered == []
    assert posted[-1].startswith("Too many requests.")
