"""A Slack listing that cannot vouch for a thread's absence must not delete it.

Three cases the restricted tier and the backfill window made real: a channel
rate-limited mid-listing, a listing stopped by the per-sync cap, and a thread
whose PARENT is older than ``SLACK_BACKFILL_DAYS`` (``conversations.history``
filters on the parent ts, so it is never listed). Plus the channel-reply
wording of the withheld notice.
"""

from __future__ import annotations

import time

import pytest

from app.ingestion.pipeline import _removable
from app.rag.access_notice import restricted_notice
from app.sources import SlackAdapter
from app.sources import slack as slack_mod
from app.sources.slack import clear_listing_cache

from .test_slack_source import FakeResponse, _msg


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    clear_listing_cache()
    monkeypatch.setattr(slack_mod.time, "sleep", lambda s: None)
    yield
    clear_listing_cache()


def _now_ts(days_ago: float = 0) -> str:
    return f"{time.time() - days_ago * 86400:.6f}"


def _fake(fail_channel: str | None = None):
    recent = _now_ts(1)

    def fake_get(url, *, params=None, headers=None, timeout=None):
        if url.endswith("conversations.list"):
            return FakeResponse({"ok": True, "channels": [
                {"id": "C1", "name": "a", "is_private": False},
                {"id": "C2", "name": "b", "is_private": False},
            ]})
        if url.endswith("conversations.history"):
            if params["channel"] == fail_channel:
                return FakeResponse({}, status_code=429, headers={"Retry-After": "60"})
            return FakeResponse({"ok": True, "messages": [
                _msg(recent, "A thread long enough to index", reply_count=1),
            ]})
        raise AssertionError(url)

    return fake_get


def test_a_channel_failing_midway_marks_the_listing_incomplete(monkeypatch):
    monkeypatch.setattr("app.sources.slack.httpx.get", _fake(fail_channel="C2"))
    adapter = SlackAdapter(token="xoxb", channel_ids=["C1", "C2"])
    refs = adapter.list_documents()
    assert [r.external_id.split(":")[0] for r in refs] == ["C1"]  # C1 is not lost
    assert adapter.listing_complete is False
    assert _removable(adapter, ["C2:1.0"], provider="slack") == []


def test_a_complete_listing_still_removes(monkeypatch):
    monkeypatch.setattr("app.sources.slack.httpx.get", _fake())
    adapter = SlackAdapter(token="xoxb", channel_ids=["C1", "C2"])
    adapter.list_documents()
    assert adapter.listing_complete is True
    gone = f"C1:{_now_ts(2)}"
    assert _removable(adapter, [gone], provider="slack") == [gone]


def test_retry_after_is_honoured_up_to_a_minute(monkeypatch):
    waits = []
    monkeypatch.setattr(slack_mod.time, "sleep", waits.append)
    monkeypatch.setattr("app.sources.slack.httpx.get", _fake(fail_channel="C2"))
    SlackAdapter(token="xoxb", channel_ids=["C1", "C2"]).list_documents()
    assert waits and max(waits) == 60


def test_threads_older_than_the_window_are_not_deleted():
    adapter = SlackAdapter(token="xoxb", channel_ids=["C1"])
    days = adapter._settings.backfill_days
    old, recent = f"C1:{_now_ts(days + 5)}", f"C1:{_now_ts(1)}"
    unpicked = f"C9:{_now_ts(1)}"
    assert _removable(adapter, [old, recent, unpicked], provider="slack") == [recent, unpicked]


def test_adapters_without_the_hooks_are_unchanged():
    assert _removable(object(), ["a", "b"], provider="notion") == ["a", "b"]


def test_a_channel_reply_is_not_told_its_asker_lacks_access():
    text = restricted_notice("slack", org_id="o", workspace_id=None, in_channel=True)
    assert "shared with you" not in text
    assert "direct message" in text
