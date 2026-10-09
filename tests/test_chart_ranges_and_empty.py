"""The asker's time range, and an empty chart that says WHY it is empty.

Staging: "pull requests merged each week over the last one year" replied "no
PRs in the last 45 days" -- the range was ignored and an empty chart was
stepped to a finer period, shrinking the window. And "this simply has not
happened" was said of a space whose GitHub app could not read pull requests.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agent import insights_agent
from app.agent.insights_agent import InsightsAgent
from app.db import get_connection
from app.insights import registry, resolve
from app.insights.resolve import ChartSpec
from app.vectorstore.base import Viewer
from .conftest import requires_db
from .test_insights_resolve import FakeLLM


def _reply(**extra):
    return FakeLLM(json.dumps({"intent": "chart", "metric": "prs_merged", "group_by": None,
                               "period": "week", "chart": "line", **extra}))


def test_a_range_they_named_is_kept_and_bounded():
    q = "How many pull requests were merged each week over the last one year?"
    intent = resolve.classify_question(q, providers=["github"], fail_open=False,
                                       llm=_reply(range_words="over the last one year", days=365))
    assert intent.spec.days == 365
    assert resolve.spec_from_dict(resolve.spec_to_dict(intent.spec)).days == 365
    huge = resolve.classify_question(q, providers=["github"], fail_open=False,
                                     llm=_reply(range_words="over the last one year", days=99999))
    assert huge.spec.days == resolve.MAX_RANGE_DAYS


def test_a_range_nobody_named_is_never_used():
    q = "How many pull requests were merged each week?"
    intent = resolve.classify_question(q, providers=["github"], fail_open=False,
                                       llm=_reply(range_words="over the last year", days=365))
    assert intent.spec.days is None


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        org = str(conn.execute("INSERT INTO organizations (name) VALUES (%s) RETURNING id",
                               (f"range-{uuid.uuid4().hex[:8]}",)).fetchone()[0])
        conn.commit()
    org_cleanup.append(org)
    return org


def _merge(org, days_ago):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO activity_facts (org_id, provider, kind, actor, subject, occurred_at, "
            "value, external_id) VALUES (%s, 'github', 'pr_merged', 'ada', 'acme/api', %s, "
            "3600, %s)",
            (org, datetime.now(timezone.utc) - timedelta(days=days_ago), uuid.uuid4().hex))
        conn.commit()


@requires_db
def test_a_year_long_range_finds_merges_the_default_window_misses(org):
    _merge(org, 200)
    _merge(org, 150)
    agent = InsightsAgent()
    everyone = Viewer.unrestricted()
    default = agent.answer("q", org, viewer=everyone, user_id="u", role="member",
                           spec=ChartSpec(metric="prs_merged", group_by=None, period="week",
                                          chart="line"))
    assert not default.chart or not any(p["value"] for p in default.chart["points"])
    year = agent.answer("q", org, viewer=everyone, user_id="u", role="member",
                        spec=ChartSpec(metric="prs_merged", group_by=None, period="week",
                                       chart="line", days=365))
    assert sum(p["value"] for p in year.chart["points"]) == 2
    assert "last 365 days" in year.chart["title"]


class _Reader:
    """`newest`: when the newest pull request was merged (None = never),
    `exists`: whether any pull request exists at all."""

    def __init__(self, *, forbidden=False, newest=None, exists=True):
        self.forbidden, self.newest, self.exists = forbidden, newest, exists

    def list_repos(self):
        return [SimpleNamespace(full_name=f"acme/r{i}") for i in range(2)]

    def list_pull_requests(self, repo, *, limit=1, state="all", **kw):
        if self.forbidden:
            raise PermissionError("403")
        if state == "merged":
            items = [SimpleNamespace(merged_at=self.newest, created_at=self.newest)] \
                if self.newest else []
        else:
            items = [SimpleNamespace(merged_at=None, created_at=self.newest)] if self.exists else []
        return SimpleNamespace(items=items)


NOW = datetime.now(timezone.utc)


@pytest.mark.parametrize("reader,needle", [
    (_Reader(forbidden=True), "Pull requests: Read"),
    (_Reader(newest=None, exists=False), "have no pull requests on GitHub"),
    (_Reader(newest=None, exists=True), "none has been merged yet"),
    (_Reader(newest=NOW - timedelta(days=300)), "older than the 180 days"),
    (_Reader(newest=NOW - timedelta(days=3)), "has not been counted yet"),
])
def test_an_empty_pull_request_chart_says_which_cause_it_is(monkeypatch, reader, needle):
    import app.githublive as githublive

    monkeypatch.setattr(githublive, "build_github_reader", lambda *a, **k: reader)
    said = insights_agent._github_pull_diagnosis(
        registry.get("prs_merged"), org_id="o", workspace_id="w")
    assert needle in said and "acme/" not in said  # never names a repository


def test_the_probe_is_only_for_pull_request_charts():
    assert insights_agent._github_pull_diagnosis(
        registry.get("commits_by_author"), org_id="o", workspace_id=None) is None


def test_the_reply_names_the_public_repos_it_checked_never_private_ones(monkeypatch):
    """Staging: a PR merged in 18-sana/rag-engine, which the space was never
    connected to, read as "none has been merged yet" with no hint why."""
    import app.githublive as githublive

    class Mixed(_Reader):
        def list_repos(self):
            return [SimpleNamespace(full_name="acme/dao", private=False),
                    SimpleNamespace(full_name="acme/secret", private=True)]

    monkeypatch.setattr(githublive, "build_github_reader",
                        lambda *a, **k: Mixed(newest=None, exists=True))
    said = insights_agent._github_pull_diagnosis(
        registry.get("prs_merged"), org_id="o", workspace_id="w")
    assert "Checked: acme/dao and 1 private repository." in said
    assert "acme/secret" not in said
    assert "not connected to this space" in said
