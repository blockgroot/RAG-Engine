"""Delivery: the bot DMs the colleague and names the asker so replying is one
tap; email carries Reply-To = the asker. A delivery that fails raises, so the
asker is told and can send again."""

from __future__ import annotations

import pytest

from app.askfriend import deliver
from app.askfriend.deliver import DeliveryError, DeliveryUnknown, send_email, send_slack
from app.auth import email as email_mod
from app.config.server import EmailSettings


class _Resp:
    def __init__(self, body: dict, status: int = 200) -> None:
        self._body, self.status_code, self.text = body, status, ""

    def json(self) -> dict:
        return self._body


@pytest.fixture
def slack(monkeypatch):
    """A fake Slack: who exists, what got posted, and how a post behaves.

    ``post``: "ok" | "rejected" | "connect_error" (never left us) |
    "timeout_landed" (timed out, but Slack kept it) | "timeout_lost".
    """
    import httpx

    state = {"users": {"b@x.com": {"id": "U_B"}, "a@x.com": {"id": "U_A"}}, "posted": [],
             "history": [], "dm_ok": True, "post": "ok", "history_ok": True}

    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("conversations.history"):
            if not state["history_ok"]:
                raise httpx.ReadTimeout("slow")
            return _Resp({"ok": True, "messages": state["history"]})
        user = state["users"].get((params or {}).get("email"))
        return _Resp({"ok": True, "user": user} if user else {"ok": False, "error": "users_not_found"})

    def fake_post(url, json=None, headers=None, timeout=None):
        if url.endswith("conversations.open"):
            return _Resp({"ok": state["dm_ok"], "channel": {"id": "D123"}})
        mode = state["post"]
        if mode == "connect_error":
            raise httpx.ConnectError("refused")
        if mode in ("ok", "timeout_landed"):
            state["posted"].append(json)
            state["history"].append({"ts": "1.2", "text": json["text"], "metadata": json.get("metadata")})
        if mode.startswith("timeout"):
            raise httpx.ReadTimeout("slow")
        return _Resp({"ok": mode == "ok", "ts": "1.2", "error": None if mode == "ok" else "channel_not_found"})

    monkeypatch.setattr("app.sources.slack_utils.httpx.get", fake_get)
    monkeypatch.setattr("app.sources.slack_utils.httpx.post", fake_post)
    return state


def test_send_slack_dms_with_asker_mention(slack):
    send_slack("xoxb", "b@x.com", "a@x.com", "Rahul Verma", "Summary text", key="k1")
    msg = slack["posted"][0]
    assert msg["channel"] == "D123"
    assert msg["text"].startswith("Rahul Verma (<@U_A>) asked Handbook something it couldn't answer")
    assert "Summary text" in msg["text"] and msg["text"].endswith("Reply to <@U_A> directly.")


def test_send_slack_without_asker_in_slack_names_only(slack):
    del slack["users"]["a@x.com"]  # the asker is not in Slack
    send_slack("xoxb", "b@x.com", "a@x.com", "Rahul Verma", "s", key="k1")
    text = slack["posted"][0]["text"]
    assert text.startswith("Rahul Verma asked Handbook") and "<@" not in text


def test_send_slack_recipient_missing_raises(slack):
    with pytest.raises(DeliveryError):
        send_slack("xoxb", "nobody@x.com", "a@x.com", "Rahul", "s", key="k1")


def test_send_slack_rejected_post_is_a_definite_failure(slack):
    slack["post"] = "rejected"
    with pytest.raises(DeliveryError) as err:
        send_slack("xoxb", "b@x.com", "a@x.com", "Rahul", "s", key="k1")
    assert not isinstance(err.value, DeliveryUnknown)


def test_send_slack_connect_error_is_a_definite_failure(slack):
    slack["post"] = "connect_error"
    with pytest.raises(DeliveryError) as err:
        send_slack("xoxb", "b@x.com", "a@x.com", "Rahul", "s", key="k1")
    assert not isinstance(err.value, DeliveryUnknown)


def test_send_slack_tags_the_message_with_the_ask(slack):
    send_slack("xoxb", "b@x.com", "a@x.com", "Rahul", "s", key="k1")
    assert slack["posted"][0]["metadata"] == {"event_type": "handbook_ask_friend", "event_payload": {"key": "k1"}}


def test_timeout_but_message_landed_counts_as_sent(slack):
    slack["post"] = "timeout_landed"
    send_slack("xoxb", "b@x.com", "a@x.com", "Rahul", "s", key="k1")  # no raise
    assert len(slack["posted"]) == 1


def test_timeout_and_message_absent_is_a_definite_failure(slack):
    slack["post"] = "timeout_lost"
    with pytest.raises(DeliveryError) as err:
        send_slack("xoxb", "b@x.com", "a@x.com", "Rahul", "s", key="k1")
    assert not isinstance(err.value, DeliveryUnknown)


def test_another_asks_message_does_not_count(slack):
    slack["history"].append({"ts": "0.9", "metadata": {"event_type": "handbook_ask_friend", "event_payload": {"key": "other"}}})
    slack["post"] = "timeout_lost"
    with pytest.raises(DeliveryError):
        send_slack("xoxb", "b@x.com", "a@x.com", "Rahul", "s", key="k1")


def test_timeout_and_history_unreadable_is_unknown(slack):
    slack["post"] = "timeout_lost"
    slack["history_ok"] = False
    with pytest.raises(DeliveryUnknown):
        send_slack("xoxb", "b@x.com", "a@x.com", "Rahul", "s", key="k1")


def test_deactivated_or_bot_users_are_not_reachable(slack):
    from app.sources.slack_utils import lookup_user_id
    slack["users"]["gone@x.com"] = {"id": "U_G", "deleted": True}
    slack["users"]["bot@x.com"] = {"id": "U_BOT", "is_bot": True}
    assert lookup_user_id("xoxb", "gone@x.com") is None
    assert lookup_user_id("xoxb", "bot@x.com") is None
    assert lookup_user_id("xoxb", "b@x.com") == "U_B"


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


def _client_raising(exc):
    class Client:
        def __init__(self, timeout=None): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, headers=None, json=None): raise exc
    return Client


def test_email_api_timeout_after_sending_is_unknown(monkeypatch):
    import httpx
    monkeypatch.setattr(email_mod.httpx, "Client", _client_raising(httpx.ReadTimeout("slow")))
    settings = EmailSettings(sender="sendgrid", sendgrid_api_key="k", smtp_from="hb@x.com")
    with pytest.raises(email_mod.EmailOutcomeUnknown):
        email_mod._dispatch("b@x.com", "s", "body", settings)


def test_email_api_connect_failure_is_definite(monkeypatch):
    import httpx
    monkeypatch.setattr(email_mod.httpx, "Client", _client_raising(httpx.ConnectError("refused")))
    settings = EmailSettings(sender="resend", resend_api_key="k", smtp_from="hb@x.com")
    with pytest.raises(email_mod.ProviderError) as err:
        email_mod._dispatch("b@x.com", "s", "body", settings)
    assert not isinstance(err.value, email_mod.EmailOutcomeUnknown)


def _smtp(send_exc):
    class SMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, m): raise send_exc
    return SMTP


def test_smtp_drop_during_send_is_unknown(monkeypatch):
    import smtplib
    monkeypatch.setattr(email_mod.smtplib, "SMTP", _smtp(smtplib.SMTPServerDisconnected("gone")))
    settings = EmailSettings(sender="smtp", smtp_host="h", smtp_from="hb@x.com")
    with pytest.raises(email_mod.EmailOutcomeUnknown):
        email_mod._dispatch("b@x.com", "s", "body", settings)


def test_smtp_refused_recipient_is_definite(monkeypatch):
    import smtplib
    monkeypatch.setattr(email_mod.smtplib, "SMTP", _smtp(smtplib.SMTPRecipientsRefused({"b@x.com": (550, b"no")})))
    settings = EmailSettings(sender="smtp", smtp_host="h", smtp_from="hb@x.com")
    with pytest.raises(email_mod.ProviderError) as err:
        email_mod._dispatch("b@x.com", "s", "body", settings)
    assert not isinstance(err.value, email_mod.EmailOutcomeUnknown)


def test_send_email_unknown_becomes_delivery_unknown(monkeypatch):
    def unknown(*a, **k):
        raise email_mod.EmailOutcomeUnknown("timed out after sending")
    monkeypatch.setattr(deliver, "_dispatch", unknown)
    with pytest.raises(DeliveryUnknown):
        send_email("b@x.com", "a@x.com", "Rahul", "s")
