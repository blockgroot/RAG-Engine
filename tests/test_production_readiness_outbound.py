"""What leaves for outside services, and how much a Slack question may carry."""

from __future__ import annotations

from types import SimpleNamespace

from app.api import slack_events
from app.websearch import duckduckgo

PAN = "ABCPE1234F"


def test_a_web_search_query_never_carries_an_identifier(monkeypatch):
    sent = []

    class _DDGS:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results):
            sent.append(query)
            return []

    import ddgs
    monkeypatch.setattr(ddgs, "DDGS", _DDGS)
    duckduckgo.DuckDuckGoSearch().search(f"tax filing for PAN {PAN}")
    assert sent and PAN not in sent[0]


def test_a_slack_question_over_the_web_limit_is_refused_before_any_model(monkeypatch):
    posted, answered = [], []
    monkeypatch.setattr(slack_events, "_org_for_team", lambda team: ("org-1", None))
    monkeypatch.setattr(slack_events, "get_live_connection_token", lambda *a, **k: "tok")
    monkeypatch.setattr(slack_events, "_slack_email", lambda t, u: "a@b.com")
    monkeypatch.setattr(slack_events, "get_member_by_address",
                        lambda email, org_id: SimpleNamespace(id="user-1", email=email))
    monkeypatch.setattr(slack_events, "post_message",
                        lambda token, ch, text, ts=None: posted.append(text))
    monkeypatch.setattr(slack_events, "_answer", lambda *a, **k: answered.append(a))
    slack_events._handle(
        {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1",
         "text": "x" * (slack_events.MAX_SLACK_QUESTION_CHARS + 1), "ts": "1.1"},
        "T1",
    )
    assert posted == [slack_events._TOO_LONG]
    assert not answered
