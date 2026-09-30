"""Chart attributes (`activity_facts.attrs`, `registry.ATTRS`).

Step 2 of docs/plans/2026-09-30-open-ended-charts.md: fields a source already
returned (GitHub labels and target branch; Linear priority, estimate, labels,
project) are kept and become groupings, filters and sums. Pinned here:

- every attribute key is a bare identifier, because it is spliced into SQL as
  a JSON key literal;
- capture costs NO extra request (the fields ride queries already made);
- a tag breakdown counts an item under each tag, keeps untagged items, and
  says so in the caveat;
- a re-sync fills attrs on rows written before the column existed.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from psycopg.types.json import Jsonb

from app.agent import insights_agent
from app.db import get_connection
from app.githublive.rest import _pull_request
from app.insights import github_facts, linear_facts, query, registry, store
from app.insights.resolve import ChartSpec
from app.sources import linear as linear_source
from .conftest import requires_db


# --------------------------------------------------------------------------
# Declarations -- no DB
# --------------------------------------------------------------------------


def test_every_attribute_key_is_a_bare_identifier():
    for a in registry.ATTRS:
        assert re.fullmatch(r"[a-z][a-z_]*", a.key), a
        assert a.type in registry.ATTR_TYPES, a
        assert a.key not in registry.DIMENSIONS, a  # never shadows a column


def test_every_attribute_reaches_at_least_one_metric():
    for a in registry.ATTRS:
        assert any(a in registry.attrs_for(m) for m in registry.METRICS.values()), a


def test_sentiment_reads_no_attributes():
    metric = registry.get("sentiment_by_theme")
    assert query.readable_attrs(metric) == ()
    assert query.group_dims(metric) == metric.dims


def test_a_number_attribute_is_a_measure_and_a_tag_is_a_grouping():
    metric = registry.get("issues_completed")
    assert {"total_estimate", "average_estimate"} <= set(query.measures_for(metric))
    assert {"priority", "label", "project"} <= set(query.group_dims(metric))
    assert "estimate" not in query.group_dims(metric)  # a number is not a category
    # A duration metric stays a duration: no estimate sums on cycle time.
    assert "total_estimate" not in query.measures_for(registry.get("issue_cycle_time"))


def test_the_github_parser_keeps_labels_and_base_from_the_listing():
    pull = _pull_request("acme/api", {
        "number": 7, "title": "Fix", "user": {"login": "ada"},
        "labels": [{"name": "bug"}, {"name": " "}, {"name": "p1"}, "junk"],
        "base": {"ref": "main"}, "created_at": "2026-09-01T00:00:00Z",
    })
    assert pull.labels == ("bug", "p1")
    assert pull.base == "main"
    assert github_facts._pull_attrs(pull).obj == {"label": ["bug", "p1"], "base": "main"}


def test_absent_values_are_omitted_never_stored_as_placeholders():
    pull = _pull_request("acme/api", {"number": 1, "user": {"login": "ada"}})
    assert github_facts._pull_attrs(pull).obj == {}
    attrs = linear_facts._issue_attrs(
        {"priority": "", "project": "", "estimate": None, "labels": []}
    )
    assert attrs.obj == {}


def test_linear_capture_rides_the_existing_feed_query():
    """Zero extra requests: the fields are in the ONE query the feed makes."""
    q = linear_source._RECENT_ISSUES_QUERY
    for field in ("priorityLabel", "estimate", "project { name }", "labels(first: 10)"):
        assert field in q


def test_linear_feed_maps_the_new_fields_and_drops_no_priority():
    calls = []

    class Adapter:
        def _query(self, text, variables):
            calls.append(text)
            return {"issues": {
                "nodes": [{
                    "identifier": "SYV-1", "title": "t", "url": "u",
                    "updatedAt": "2026-09-01T00:00:00Z",
                    "createdAt": "2026-08-01T00:00:00Z", "completedAt": None,
                    "state": {"name": "Todo", "type": "unstarted"},
                    "assignee": {"name": "Sana"}, "team": {"name": "Core"},
                    "priorityLabel": "No priority", "estimate": 3,
                    "project": {"name": "Q3"},
                    "labels": {"nodes": [{"name": "bug"}]},
                }],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }}

    issues = linear_source.LinearAdapter.fetch_recent_issues(
        Adapter(), datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    assert len(calls) == 1
    issue = issues[0]
    assert issue["priority"] == ""
    assert (issue["estimate"], issue["project"], issue["labels"]) == (3, "Q3", ["bug"])


# --------------------------------------------------------------------------
# SQL -- a real database
# --------------------------------------------------------------------------


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"attrs-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _fact(org_id, *, provider, kind, attrs, actor="ada", subject="Core"):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO activity_facts
                (org_id, provider, kind, actor, subject, occurred_at,
                 external_id, attrs)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (org_id, provider, kind, actor, subject,
             datetime.now(timezone.utc), uuid.uuid4().hex, Jsonb(attrs)),
        )
        conn.commit()


@pytest.fixture
def issues(org):
    for attrs in [
        {"priority": "Urgent", "label": ["bug", "backend"], "estimate": 3},
        {"priority": "Urgent", "label": ["bug"], "estimate": 5},
        {"priority": "Low", "estimate": 1},
        {},  # written before the column existed
    ]:
        _fact(org, provider="linear", kind="issue_completed", attrs=attrs)
    return org


def _run(org, **kw):
    return store.run_metric("issues_completed", org_id=org, workspace_id=None,
                            period="quarter", **kw)


@requires_db
def test_grouping_by_a_category(issues):
    got = {p.group: p.value for p in _run(issues, group_by="priority")}
    assert got == {"Urgent": 2, "Low": 1, None: 1}


@requires_db
def test_a_tag_counts_under_each_tag_and_keeps_untagged_items(issues):
    got = {p.group: p.value for p in _run(issues, group_by="label")}
    assert got == {"bug": 2, "backend": 1, None: 2}


@requires_db
def test_filters_on_a_tag_and_a_category(issues):
    assert sum(p.value for p in _run(issues, filters=(("label", "bug"),))) == 2
    assert sum(p.value for p in _run(issues, filters=(("priority", "Low"),))) == 1


@requires_db
def test_a_number_attribute_sums(issues):
    got = {p.group: p.value for p in _run(issues, group_by="priority",
                                           measure="total_estimate")}
    assert got["Urgent"] == 8 and got["Low"] == 1


@requires_db
def test_split_by_an_attribute(issues):
    got = {(p.group, p.series): p.value
           for p in _run(issues, group_by="priority", split_by="label")}
    assert got[("Urgent", "bug")] == 2 and got[("Urgent", "backend")] == 1


@requires_db
def test_list_values_of_a_tag(issues):
    assert store.list_values("issues_completed", "label", org_id=issues,
                             workspace_id=None, days=90) == ["backend", "bug"]


@requires_db
def test_the_hover_carries_declared_attributes_only(org):
    _fact(org, provider="linear", kind="issue_completed",
          attrs={"priority": "Urgent", "secret_field": "x"})
    rows = store.list_facts("issues_completed", org_id=org, workspace_id=None, days=90)
    assert rows[0].attrs == {"priority": "Urgent"}


@requires_db
def test_the_agent_titles_and_caveats_a_tag_chart(issues):
    spec = ChartSpec(metric="issues_completed", group_by="label", period="month",
                     chart="bar", filters=(("priority", "urgent"),))
    panel, _ = insights_agent._run_spec(spec, org_id=issues, workspace_id=None,
                                        user_id="", role="member")
    assert panel["title"] == "Tasks completed by label — priority: Urgent"
    assert "counts under each" in panel["caveat"]
    assert "Unknown" in panel["caveat"]


@requires_db
def test_a_resync_fills_attrs_on_old_rows(org):
    """Rows written before the column existed get `{}`; the next sync's
    upsert must fill them, or a chart by priority stays "Unknown" forever."""
    issue = {"identifier": "SYV-9", "state": "Done", "state_type": "completed",
             "assignee": "Sana", "team": "Core", "url": "https://linear.test/SYV-9",
             "created_at": datetime.now(timezone.utc) - timedelta(days=2),
             "completed_at": datetime.now(timezone.utc), "at": datetime.now(timezone.utc)}
    linear_facts._write(linear_facts._issue_rows(org, None, issue), None)
    issue.update(priority="High", labels=["bug"])
    linear_facts._write(linear_facts._issue_rows(org, None, issue), None)
    with get_connection() as conn:
        got = conn.execute(
            "SELECT DISTINCT attrs FROM activity_facts WHERE org_id = %s", (org,)
        ).fetchall()
    assert [r[0] for r in got] == [{"priority": "High", "label": ["bug"]}]
