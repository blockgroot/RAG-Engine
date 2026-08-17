"""Phase 2 (Slack Integration Plan): SlackAdapter.

No network: ``httpx.get`` is monkeypatched at the module attribute path
(``app.sources.slack.httpx.get``), mirroring ``tests/test_google_drive_source.py``'s
pattern.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.config.settings import SlackSettings
from app.core.exceptions import ConfigurationError, SourceError
from app.sources import SlackAdapter, build_source_adapter
from app.sources.base import SourceDocument, SourceRef


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._payload


def _msg(ts, text, user="U1", reply_count=0, latest_reply=None, subtype=None):
    m = {"ts": ts, "text": text, "user": user, "reply_count": reply_count}
    if latest_reply:
        m["latest_reply"] = latest_reply
    if subtype:
        m["subtype"] = subtype
    return m


# --- constructor validation -------------------------------------------------


def test_empty_token_raises_configuration_error():
    with pytest.raises(ConfigurationError):
        SlackAdapter(token="", channel_ids=["C1"])


def test_empty_channel_ids_raises_configuration_error():
    with pytest.raises(ConfigurationError):
        SlackAdapter(token="xoxb-abc", channel_ids=[])


# --- list_documents ----------------------------------------------------------


def test_list_documents_returns_one_ref_per_thread(monkeypatch):
    def fake_get(url, *, params=None, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer xoxb-abc"
        if url.endswith("conversations.history"):
            return FakeResponse(
                {
                    "ok": True,
                    "messages": [
                        _msg("100.000001", "Question about the handbook?", reply_count=3, latest_reply="100.000050"),
                    ],
                }
            )
        raise AssertionError(f"unexpected call to {url}")

    monkeypatch.setattr("app.sources.slack.httpx.get", fake_get)

    adapter = SlackAdapter(token="xoxb-abc", channel_ids=["C1"])
    refs = adapter.list_documents()

    assert len(refs) == 1
    ref = refs[0]
    assert isinstance(ref, SourceRef)
    assert ref.external_id == "C1:100.000001"
    assert ref.last_modified == datetime.fromtimestamp(100.000050, tz=timezone.utc)


def test_list_documents_filters_short_no_reply_messages(monkeypatch):
    def fake_get(url, *, params=None, headers=None, timeout=None):
        return FakeResponse(
            {
                "ok": True,
                "messages": [
                    _msg("1.0", "thanks", reply_count=0),
                    _msg("2.0", "This is a real question that deserves an answer", reply_count=0),
                ],
            }
        )

    monkeypatch.setattr("app.sources.slack.httpx.get", fake_get)
    adapter = SlackAdapter(
        token="xoxb-abc",
        channel_ids=["C1"],
        settings=SlackSettings(None, None, None, min_thread_chars=20),
    )
    refs = adapter.list_documents()

    assert len(refs) == 1
    assert refs[0].external_id == "C1:2.0"


def test_list_documents_skips_system_subtype_messages(monkeypatch):
    def fake_get(url, *, params=None, headers=None, timeout=None):
        return FakeResponse(
            {
                "ok": True,
                "messages": [_msg("1.0", "has joined the channel", subtype="channel_join")],
            }
        )

    monkeypatch.setattr("app.sources.slack.httpx.get", fake_get)
    adapter = SlackAdapter(token="xoxb-abc", channel_ids=["C1"])
    assert adapter.list_documents() == []


def test_list_documents_stops_at_max_documents_per_sync(monkeypatch):
    def fake_get(url, *, params=None, headers=None, timeout=None):
        return FakeResponse(
            {
                "ok": True,
                "messages": [
                    _msg("1.0", "first real thread", reply_count=1, latest_reply="1.5"),
                    _msg("2.0", "second real thread", reply_count=1, latest_reply="2.5"),
                ],
            }
        )

    monkeypatch.setattr("app.sources.slack.httpx.get", fake_get)
    adapter = SlackAdapter(
        token="xoxb-abc",
        channel_ids=["C1"],
        settings=SlackSettings(None, None, None, max_documents_per_sync=1),
    )
    refs = adapter.list_documents()
    assert len(refs) == 1


def test_list_documents_raises_source_error_on_slack_ok_false(monkeypatch):
    monkeypatch.setattr(
        "app.sources.slack.httpx.get",
        lambda url, **kwargs: FakeResponse({"ok": False, "error": "not_in_channel"}),
    )
    adapter = SlackAdapter(token="xoxb-abc", channel_ids=["C1"])
    with pytest.raises(SourceError, match="not_in_channel"):
        adapter.list_documents()


# --- fetch_document ------------------------------------------------------


def test_fetch_document_renders_thread_with_display_names(monkeypatch):
    def fake_get(url, *, params=None, headers=None, timeout=None):
        if url.endswith("conversations.replies"):
            return FakeResponse(
                {
                    "ok": True,
                    "messages": [
                        _msg("100.0", "What is the leave policy?", user="U1"),
                        _msg("101.0", "15 days per year.", user="U2"),
                    ],
                }
            )
        if url.endswith("users.info"):
            names = {"U1": "Alice", "U2": "Bob"}
            return FakeResponse({"ok": True, "user": {"real_name": names[params["user"]]}})
        raise AssertionError(f"unexpected call to {url}")

    monkeypatch.setattr("app.sources.slack.httpx.get", fake_get)
    adapter = SlackAdapter(token="xoxb-abc", channel_ids=["C1"])
    doc = adapter.fetch_document("C1:100.0")

    assert isinstance(doc, SourceDocument)
    assert "Alice" in doc.content
    assert "Bob" in doc.content
    assert "15 days per year" in doc.content
    assert doc.source_uri.startswith("https://slack.com/archives/C1/p")
    assert doc.last_modified == datetime.fromtimestamp(101.0, tz=timezone.utc)


def test_fetch_document_truncates_oversized_thread(monkeypatch):
    messages = [_msg(f"{i}.0", f"message {i}", user="U1") for i in range(10)]

    monkeypatch.setattr(
        "app.sources.slack.httpx.get",
        lambda url, **kwargs: (
            FakeResponse({"ok": True, "messages": messages})
            if url.endswith("conversations.replies")
            else FakeResponse({"ok": True, "user": {"real_name": "Alice"}})
        ),
    )
    adapter = SlackAdapter(
        token="xoxb-abc",
        channel_ids=["C1"],
        settings=SlackSettings(None, None, None, max_thread_messages=3),
    )
    doc = adapter.fetch_document("C1:0.0")

    assert "truncated" in doc.content
    assert "message 0" in doc.content  # root kept
    assert "message 9" in doc.content  # most recent tail kept
    assert "message 5" not in doc.content  # middle dropped


def test_fetch_document_raises_on_malformed_external_id():
    adapter = SlackAdapter(token="xoxb-abc", channel_ids=["C1"])
    with pytest.raises(SourceError):
        adapter.fetch_document("not-a-valid-id")


def test_fetch_document_raises_when_thread_has_no_messages(monkeypatch):
    monkeypatch.setattr(
        "app.sources.slack.httpx.get",
        lambda url, **kwargs: FakeResponse({"ok": True, "messages": []}),
    )
    adapter = SlackAdapter(token="xoxb-abc", channel_ids=["C1"])
    with pytest.raises(SourceError):
        adapter.fetch_document("C1:100.0")


# --- get_last_modified -----------------------------------------------------


def test_get_last_modified_returns_latest_message_ts(monkeypatch):
    monkeypatch.setattr(
        "app.sources.slack.httpx.get",
        lambda url, **kwargs: FakeResponse(
            {"ok": True, "messages": [_msg("1.0", "a"), _msg("2.0", "b")]}
        ),
    )
    adapter = SlackAdapter(token="xoxb-abc", channel_ids=["C1"])
    assert adapter.get_last_modified("C1:1.0") == datetime.fromtimestamp(2.0, tz=timezone.utc)


def test_get_last_modified_returns_none_when_thread_missing(monkeypatch):
    monkeypatch.setattr(
        "app.sources.slack.httpx.get",
        lambda url, **kwargs: FakeResponse({"ok": True, "messages": []}),
    )
    adapter = SlackAdapter(token="xoxb-abc", channel_ids=["C1"])
    assert adapter.get_last_modified("C1:1.0") is None


# --- build_source_adapter factory -------------------------------------------


def test_build_source_adapter_returns_slack_adapter():
    adapter = build_source_adapter(
        "slack", token="xoxb-abc", config={"channel_ids": ["C1", "C2"]}
    )
    assert isinstance(adapter, SlackAdapter)


def test_build_source_adapter_slack_requires_token():
    with pytest.raises(ConfigurationError):
        build_source_adapter("slack", token=None, config={"channel_ids": ["C1"]})


def test_build_source_adapter_slack_requires_channel_ids():
    with pytest.raises(ConfigurationError):
        build_source_adapter("slack", token="xoxb-abc", config={})
