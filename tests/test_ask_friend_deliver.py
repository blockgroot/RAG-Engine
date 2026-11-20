"""Delivery: the bot DMs the colleague and names the asker so replying is one
tap; email carries Reply-To = the asker. A delivery that fails raises, so the
asker is told and can send again."""

from __future__ import annotations

import pytest

from app.askfriend import deliver
from app.askfriend.deliver import DeliveryError, send_email, send_slack
from app.auth import email as email_mod
from app.config.server import EmailSettings


class _Resp:
    def __init__(self, body: dict, status: int = 200) -> None:
        self._body, self.status_code, self.text = body, status, ""

    def json(self) -> dict:
        return self._body


@pytest.fixture
def slack(monkeypatch):
    """A fake Slack: who exists, and what got posted."""
    state = {"users": {"b@x.com": "U_B", "a@x.com": "U_A"}, "posted": [], "dm_ok": True, "post_ok": True}

    def fake_get(url, params=None, headers=None, timeout=None):
        uid = state["users"].get((params or {}).get("email"))
        return _Resp({"ok": True, "user": {"id": uid}} if uid else {"ok": False, "error": "users_not_found"})

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("conversations.open"):
            return _Resp({"ok": state["dm_ok"], "channel": {"id": "D123"}})
        state["posted"].append(json)
        return _Resp({"ok": state["post_ok"], "ts": "1.2"})

    monkeypatch.setattr("app.sources.slack_utils.httpx.get", fake_get)
    monkeypatch.setattr("app.sources.slack_utils.httpx.post", fake_post)
    return state


def test_send_slack_dms_with_asker_mention(slack):
    send_slack("xoxb", "b@x.com", "a@x.com", "Rahul Verma", "Summary text")
    msg = slack["posted"][0]
    assert msg["channel"] == "D123"
    assert msg["text"].startswith("Rahul Verma (<@U_A>) asked Handbook something it couldn't answer")
    assert "Summary text" in msg["text"] and msg["text"].endswith("Reply to <@U_A> directly.")


def test_send_slack_without_asker_in_slack_names_only(slack):
    del slack["users"]["a@x.com"]
    send_slack("xoxb", "b@x.com", "a@x.com", "Rahul Verma", "s")
    text = slack["posted"][0]["text"]
    assert text.startswith("Rahul Verma asked Handbook") and "<@" not in text


def test_send_slack_recipient_missing_raises(slack):
    with pytest.raises(DeliveryError):
        send_slack("xoxb", "nobody@x.com", "a@x.com", "Rahul", "s")


def test_send_slack_failed_post_raises(slack):
    slack["post_ok"] = False
    with pytest.raises(DeliveryError):
        send_slack("xoxb", "b@x.com", "a@x.com", "Rahul", "s")


def test_send_email_sets_reply_to(monkeypatch):
    sent = {}
    monkeypatch.setattr(deliver, "_dispatch", lambda to, subject, body, settings, **kw: sent.update(
        to=to, subject=subject, body=body, **kw))
    send_email("b@x.com", "a@x.com", "Rahul Verma", "s")
    assert sent["to"] == "b@x.com" and sent["reply_to"] == "a@x.com"
    assert sent["subject"] == "Rahul Verma asked you a question via Handbook"
    assert sent["body"].endswith("Just reply to this email to answer Rahul Verma.")


def test_send_email_failure_raises_delivery_error(monkeypatch):
    def boom(*a, **k):
        raise email_mod.ProviderError("smtp down")
    monkeypatch.setattr(deliver, "_dispatch", boom)
    with pytest.raises(DeliveryError):
        send_email("b@x.com", "a@x.com", "Rahul", "s")


def test_sendgrid_payload_carries_reply_to(monkeypatch):
    captured = {}

    class Client:
        def __init__(self, timeout=None): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None, json=None):
            captured.update(json)
            return _Resp({}, 202)

    monkeypatch.setattr(email_mod.httpx, "Client", Client)
    settings = EmailSettings(sender="sendgrid", sendgrid_api_key="k", smtp_from="Handbook <hb@x.com>")
    email_mod._dispatch("b@x.com", "s", "body", settings, reply_to="a@x.com")
    assert captured["reply_to"] == {"email": "a@x.com"}


def test_resend_payload_carries_reply_to(monkeypatch):
    captured = {}

    class Client:
        def __init__(self, timeout=None): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None, json=None):
            captured.update(json)
            return _Resp({}, 200)

    monkeypatch.setattr(email_mod.httpx, "Client", Client)
    settings = EmailSettings(sender="resend", resend_api_key="k", smtp_from="hb@x.com")
    email_mod._dispatch("b@x.com", "s", "body", settings, reply_to="a@x.com")
    assert captured["reply_to"] == "a@x.com"
