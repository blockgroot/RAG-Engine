"""A GitHub chart reads GitHub when it is asked, not only on the hourly timer.

GitHub has no "count merged PRs per week" call, so counting stays SQL over
stored rows; what changed is WHEN they are read.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from app.config.settings import GitHubLiveSettings
from app.insights import github_facts

QUICK = GitHubLiveSettings(chart_refresh_minutes=10, chart_refresh_wait_seconds=1.0)


def _patch(monkeypatch, last, read=lambda *a: None):
    calls = []
    monkeypatch.setattr(github_facts, "_connection", lambda o, w: ("conn-1", last))
    monkeypatch.setattr(github_facts, "_read_now",
                        lambda o, w, c: (calls.append((o, w, c)), read())[1])
    github_facts._RUNNING.clear()
    return calls


def test_a_recent_read_is_reused(monkeypatch):
    calls = _patch(monkeypatch, datetime.now(timezone.utc) - timedelta(minutes=2))
    assert github_facts.refresh_for_chart("o", "w", QUICK) == "fresh"
    assert calls == []


def test_a_stale_read_is_redone_before_the_chart(monkeypatch):
    calls = _patch(monkeypatch, datetime.now(timezone.utc) - timedelta(hours=3))
    assert github_facts.refresh_for_chart("o", "w", QUICK) == "refreshed"
    assert calls == [("o", "w", "conn-1")]


def test_never_read_is_read(monkeypatch):
    calls = _patch(monkeypatch, None)
    assert github_facts.refresh_for_chart("o", None, QUICK) == "refreshed"
    assert len(calls) == 1


def test_a_slow_read_answers_from_what_is_stored_and_says_so(monkeypatch):
    _patch(monkeypatch, None, read=lambda: time.sleep(3))
    slow = GitHubLiveSettings(chart_refresh_wait_seconds=0.2)
    assert github_facts.refresh_for_chart("o", "w", slow) == "reading"


def test_a_failed_read_never_fails_the_chart(monkeypatch):
    def boom():
        raise RuntimeError("github down")
    _patch(monkeypatch, None, read=boom)
    assert github_facts.refresh_for_chart("o", "w", QUICK) == "failed"


def test_no_github_connection_is_no_read(monkeypatch):
    monkeypatch.setattr(github_facts, "_connection", lambda o, w: None)
    assert github_facts.refresh_for_chart("o", "w", QUICK) == "none"


def test_the_chart_says_when_github_was_not_fully_read(monkeypatch):
    from app.agent import insights_agent
    from app.insights import registry

    monkeypatch.setattr(github_facts, "refresh_for_chart", lambda o, w: "reading")
    note = insights_agent._read_github_now(registry.get("prs_merged"), org_id="o",
                                           workspace_id="w")
    assert "still being read" in note
    assert insights_agent._read_github_now(registry.get("issues_completed"), org_id="o",
                                           workspace_id="w") is None
