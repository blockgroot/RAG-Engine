"""Phase 3 (Slack Integration Plan): slack_utils.py channel picker plumbing.

No network: ``httpx.get``/``httpx.post`` are monkeypatched, mirroring
``tests/test_google_drive_source.py``'s pattern for ``google_drive_utils.py``.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import ConfigurationError, SourceError
from app.sources.slack_utils import (
    join_public_channels,
    list_slack_channels,
    validate_slack_channels,
)


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


def _channel(id, name, is_private=False, is_member=False):
    return {"id": id, "name": name, "is_private": is_private, "is_member": is_member}


def test_list_slack_channels_returns_normalized_shape(monkeypatch):
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.get",
        lambda url, **kwargs: FakeResponse(
            {
                "ok": True,
                "channels": [
                    _channel("C1", "general", is_member=True),
                    _channel("C2", "leadership-private", is_private=True, is_member=False),
                ],
            }
        ),
    )
    channels = list_slack_channels("xoxb-abc")
    assert channels == [
        {"id": "C1", "name": "general", "is_private": False, "is_member": True},
        {"id": "C2", "name": "leadership-private", "is_private": True, "is_member": False},
    ]


def test_list_slack_channels_paginates(monkeypatch):
    calls = []

    def fake_get(url, *, params=None, headers=None, timeout=None):
        calls.append(params)
        if not params.get("cursor"):
            return FakeResponse(
                {
                    "ok": True,
                    "channels": [_channel("C1", "general")],
                    "response_metadata": {"next_cursor": "page2"},
                }
            )
        return FakeResponse({"ok": True, "channels": [_channel("C2", "eng")]})

    monkeypatch.setattr("app.sources.slack_utils.httpx.get", fake_get)
    channels = list_slack_channels("xoxb-abc")
    assert [c["id"] for c in channels] == ["C1", "C2"]
    assert len(calls) == 2


def test_list_slack_channels_raises_on_slack_ok_false(monkeypatch):
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.get",
        lambda url, **kwargs: FakeResponse({"ok": False, "error": "invalid_auth"}),
    )
    with pytest.raises(SourceError, match="invalid_auth"):
        list_slack_channels("xoxb-bad")


def test_validate_slack_channels_accepts_known_ids(monkeypatch):
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.get",
        lambda url, **kwargs: FakeResponse(
            {"ok": True, "channels": [_channel("C1", "general"), _channel("C2", "eng")]}
        ),
    )
    config = validate_slack_channels("xoxb-abc", ["C1", "C2"])
    assert config == {
        "channel_ids": ["C1", "C2"],
        "channel_names": {"C1": "general", "C2": "eng"},
    }


def test_validate_slack_channels_rejects_empty_selection():
    with pytest.raises(ConfigurationError):
        validate_slack_channels("xoxb-abc", [])


def test_validate_slack_channels_rejects_unknown_id(monkeypatch):
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.get",
        lambda url, **kwargs: FakeResponse({"ok": True, "channels": [_channel("C1", "general")]}),
    )
    with pytest.raises(ConfigurationError, match="C-does-not-exist"):
        validate_slack_channels("xoxb-abc", ["C1", "C-does-not-exist"])


def test_join_public_channels_only_joins_public_non_members(monkeypatch):
    joined = []

    def fake_get(url, **kwargs):
        return FakeResponse(
            {
                "ok": True,
                "channels": [
                    _channel("C1", "general", is_member=False),  # public, not joined -> join
                    _channel("C2", "eng", is_member=True),  # public, already joined -> skip
                    _channel("C3", "secret", is_private=True, is_member=False),  # private -> skip
                ],
            }
        )

    def fake_post(url, *, data=None, headers=None, timeout=None):
        joined.append(data["channel"])
        return FakeResponse({"ok": True})

    monkeypatch.setattr("app.sources.slack_utils.httpx.get", fake_get)
    monkeypatch.setattr("app.sources.slack_utils.httpx.post", fake_post)

    join_public_channels("xoxb-abc", ["C1", "C2", "C3"])

    assert joined == ["C1"]


def test_join_public_channels_swallows_per_channel_failures(monkeypatch):
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.get",
        lambda url, **kwargs: FakeResponse(
            {"ok": True, "channels": [_channel("C1", "general", is_member=False)]}
        ),
    )
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.post",
        lambda url, **kwargs: FakeResponse({"ok": False, "error": "is_archived"}),
    )
    # Must not raise — best-effort, per the picker's re-check being the source of truth.
    join_public_channels("xoxb-abc", ["C1"])
