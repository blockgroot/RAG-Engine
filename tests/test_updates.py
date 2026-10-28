"""New-version notices and the self-hosted Update button.

What is pinned: which releases count as newer, that Cloud never offers a
button, that no button is offered across a release needing steps by hand,
and that the updater re-checks the version itself rather than trusting the
API. The release list is faked; nothing here reaches the network.
"""

from __future__ import annotations

import importlib.util
import io
import json
from datetime import date
from pathlib import Path

import pytest

from app.config.settings import UpdateSettings
from app.updates import check
from scripts import release_feed

TODAY = date.today().isoformat()
FEED = {
    "releases": [
        {"version": "1.10.0", "date": TODAY, "notes": ["Charts"], "notes_url": "https://x/1.10.0"},
        {"version": "1.9.0", "date": "2026-01-01", "security": True, "notes": ["Fix"]},
        {"version": "1.2.0", "date": TODAY, "notes": ["Old"]},
        {"version": "not-a-version"},
    ]
}


@pytest.fixture
def feed(monkeypatch):
    releases = check.parse_feed(FEED)
    monkeypatch.setattr(check, "fetch_releases", lambda settings: releases)
    return releases


def settings(**kw) -> UpdateSettings:
    base = dict(deployment="selfhosted", current_version="1.2.0",
                updater_url="http://updater:8080", updater_token="t")
    return UpdateSettings(**{**base, **kw})


def test_versions_compare_as_numbers_not_text(feed):
    assert [r.version for r in feed] == ["1.10.0", "1.9.0", "1.2.0"]
    status = check.update_status(settings())
    assert [r.version for r in status.newer] == ["1.9.0", "1.10.0"]
    assert status.latest.version == "1.10.0"
    assert status.can_update and status.blocked_reason is None


def test_cloud_tells_but_never_offers_a_button(feed):
    status = check.update_status(settings(deployment="cloud", current_version="1.10.0"))
    assert status.newer == [] and not status.can_update
    assert status.whats_new.version == "1.10.0"


def test_no_button_across_a_release_that_needs_steps(monkeypatch):
    data = {"releases": [{"version": "1.3.0", "date": TODAY, "manual_steps": True},
                         {"version": "1.4.0", "date": TODAY}]}
    releases = check.parse_feed(data)
    monkeypatch.setattr(check, "fetch_releases", lambda s: releases)
    status = check.update_status(settings())
    assert not status.can_update and "steps on your server" in status.blocked_reason


def test_no_button_without_the_updater(feed):
    status = check.update_status(settings(updater_token=None))
    assert not status.can_update and "updater is not set up" in status.blocked_reason


def test_check_off_or_unknown_version_shows_nothing(feed):
    assert check.update_status(settings(check_enabled=False)).newer == []
    assert check.update_status(settings(current_version=None)).newer == []


def test_only_https_notes_links_are_kept():
    (r,) = check.parse_feed({"releases": [{"version": "1.0.0", "notes_url": "javascript:x"}]})
    assert r.notes_url is None


def test_start_update_refuses_a_version_not_offered(feed):
    with pytest.raises(check.UpdateError):
        check.start_update("9.9.9", settings())


def test_release_notes_need_both_flags_and_a_point():
    flags, points, _ = release_feed.parse_notes(
        "security: false\nmanual_steps: true\n\n- One\n- Two\n"
    )
    assert flags == {"security": False, "manual_steps": True} and points == ["One", "Two"]
    with pytest.raises(ValueError):
        release_feed.parse_notes("security: false\n\n- One\n")
    with pytest.raises(ValueError):
        release_feed.parse_notes("security: false\nmanual_steps: false\n\nprose only\n")


def test_the_feed_stays_newest_first_and_replaces_a_rerun():
    flags = {"security": False, "manual_steps": False}
    feed = release_feed.add_release({}, "1.9.0", flags, ["a"], TODAY)
    feed = release_feed.add_release(feed, "1.10.0", flags, ["b"], TODAY)
    feed = release_feed.add_release(feed, "1.9.0", flags, ["c"], TODAY)
    assert [r["version"] for r in feed["releases"]] == ["1.10.0", "1.9.0"]
    assert feed["releases"][1]["notes"] == ["c"]


@pytest.fixture
def updater(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parent.parent / "deploy" / "updater" / "updater.py"
    spec = importlib.util.spec_from_file_location("hb_updater", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module.urllib.request, "urlopen",
        lambda url, timeout: io.BytesIO(json.dumps(FEED).encode()),
    )
    env = tmp_path / ".env"
    env.write_text("HANDBOOK_VERSION=1.2.0\nOTHER=x\n")
    monkeypatch.setattr(module, "ENV_FILE", str(env))
    return module


def test_the_updater_checks_the_version_itself(updater):
    assert updater.check_version("1.10.0", "1.2.0") is None
    assert "not newer" in updater.check_version("1.2.0", "1.2.0")
    assert "not a Handbook release" in updater.check_version("1.11.0", "1.2.0")
    assert updater.check_version("1.10.0; rm -rf /", "1.2.0") == "Not a version number."


def test_the_updater_edits_only_the_version_line(updater):
    updater.write_env("HANDBOOK_VERSION", "1.10.0")
    assert updater.read_env("HANDBOOK_VERSION") == "1.10.0"
    assert updater.read_env("OTHER") == "x"
