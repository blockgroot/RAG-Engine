"""Slack charts as images: the upload calls, and the text fallback."""

from __future__ import annotations

from app.api import slack_events
from app.sources import slack_utils


class _Resp:
    def __init__(self, body=None, status=200):
        self._body = body or {}
        self.status_code = status

    def json(self):
        return self._body


def _fake_slack(monkeypatch, complete_error=None, url_error=None):
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        if url.endswith("files.getUploadURLExternal"):
            if url_error:
                return _Resp({"ok": False, "error": url_error})
            return _Resp({"ok": True, "upload_url": "https://files.slack.test/up", "file_id": "F1"})
        if url == "https://files.slack.test/up":
            return _Resp()
        return _Resp({"ok": not complete_error, "error": complete_error})

    monkeypatch.setattr(slack_utils.httpx, "post", post)
    return calls


def test_upload_is_three_calls_into_the_thread(monkeypatch):
    calls = _fake_slack(monkeypatch)
    status = slack_utils.upload_file("tok", "C1", b"png", filename="chart.png", title="T",
                                     alt_text="alt", thread_ts="111")
    assert status == "ok"
    assert [c[0].rsplit("/", 1)[-1] for c in calls] == [
        "files.getUploadURLExternal", "up", "files.completeUploadExternal"]
    assert calls[0][1]["data"] == {"filename": "chart.png", "length": "3", "alt_txt": "alt"}
    assert calls[1][1]["content"] == b"png"
    assert calls[2][1]["json"] == {"files": [{"id": "F1", "title": "T"}],
                                   "channel_id": "C1", "thread_ts": "111"}


def test_missing_scope_is_reported(monkeypatch):
    _fake_slack(monkeypatch, url_error="missing_scope")
    assert slack_utils.upload_file("tok", "C1", b"png", filename="f", title="T",
                                   alt_text="a") == "missing_scope"


def test_any_other_failure_is_an_error_never_a_raise(monkeypatch):
    _fake_slack(monkeypatch, complete_error="channel_not_found")
    assert slack_utils.upload_file("tok", "C1", b"png", filename="f", title="T",
                                   alt_text="a") == "error"

    def boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(slack_utils.httpx, "post", boom)
    assert slack_utils.upload_file("tok", "C1", b"png", filename="f", title="T",
                                   alt_text="a") == "error"


PANEL = {"title": "Commits by author", "chart": "bar", "explain": "One bar per author",
         "points": [{"bucket": "", "group": "ana", "series": None, "value": 3}]}


def _run_handle(monkeypatch, upload_status):
    monkeypatch.setattr(slack_events, "_org_for_team", lambda team: ("org-1", None))
    monkeypatch.setattr(slack_events, "get_live_connection_token", lambda *a, **k: "tok")
    monkeypatch.setattr(slack_events, "_slack_email", lambda t, u: "a@b.com")
    monkeypatch.setattr(slack_events, "get_member_by_address", lambda email, org_id: type(
        "U", (), {"id": "u1", "org_id": "org-1", "email": "a@b.com"})())
    monkeypatch.setattr(slack_events, "_scope_for_channel", lambda *a: None)
    monkeypatch.setattr(slack_events, "viewer_for_person", lambda *a: None)
    monkeypatch.setattr(slack_events, "_answer", lambda *a, **k: slack_events.SlackAnswer(
        "Commits by author", chart=PANEL, with_values="Commits by author\n•   ana: 3"))
    monkeypatch.setattr(slack_events, "post_message", lambda tok, ch, text, ts=None: "msg-1")
    edited, uploads = [], []
    monkeypatch.setattr(slack_events, "update_message",
                        lambda tok, ch, ts, text: edited.append(text) or True)

    def upload(tok, ch, data, **kw):
        uploads.append((ch, data, kw))
        return upload_status

    monkeypatch.setattr(slack_events, "upload_file", upload)
    slack_events._handle(
        {"type": "app_mention", "channel": "C1", "user": "U1", "text": "q", "ts": "111"}, "T1")
    return edited, uploads


def test_a_chart_is_uploaded_under_the_caption(monkeypatch):
    edited, uploads = _run_handle(monkeypatch, "ok")
    assert edited == ["Commits by author"]
    (channel, data, kw), = uploads
    assert channel == "C1" and data.startswith(b"\x89PNG")
    assert kw["thread_ts"] == "111" and kw["alt_text"] == "One bar per author"


def test_missing_scope_falls_back_to_values_and_says_reconnect(monkeypatch):
    edited, _ = _run_handle(monkeypatch, "missing_scope")
    assert edited[-1] == ("Commits by author\n•   ana: 3\n" + slack_events._RECONNECT_FOR_IMAGES)


def test_other_failures_fall_back_to_values_only(monkeypatch):
    edited, _ = _run_handle(monkeypatch, "error")
    assert edited[-1] == "Commits by author\n•   ana: 3"
