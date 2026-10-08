"""Every simple field a source returns, kept and DISCOVERED for charts.

Step 2 of docs/plans/2026-09-30-open-ended-charts.md. It first kept six
hand-picked fields, which left the original problem in place: a field nobody
listed (a PR's milestone, a Linear cycle) was dropped at sync and could never
be charted. Now the writers keep every SIMPLE field (`insights/fields.py`) and
`attr_catalog` reads which ones exist. Pinned here:

- the size/privacy rules: no bodies, ids, links, timestamps or emails; a
  nested object becomes its name; at most MAX_FIELDS keys;
- capture costs NO extra request (Linear's query is the same single call and
  validates against the published schema shape it was written from);
- discovery types a field from the data, refuses identifier-like text, and is
  viewer- and scope-filtered;
- a field that exists only in the data -- in no hint -- is chartable end to end;
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
from app.insights import attr_catalog, github_facts, linear_facts, query, registry, store
from app.insights.fields import KEY_RE, MAX_FIELDS, normalize_key, simple_fields
from app.insights.resolve import ChartSpec
from app.sources import linear as linear_source
from .conftest import requires_db


# --------------------------------------------------------------------------
# Capture rules -- no DB
# --------------------------------------------------------------------------


PR_PAYLOAD = {
    "id": 1, "node_id": "PR_x", "number": 7, "title": "Fix login", "body": "x" * 5000,
    "state": "open", "draft": False, "locked": False,
    "user": {"login": "ada", "avatar_url": "https://a"},
    "labels": [{"name": "bug", "color": "f00"}, {"name": "p1"}],
    "milestone": {"title": "v2.0", "number": 3},
    "assignees": [{"login": "bo"}], "requested_reviewers": [],
    "base": {"ref": "main", "repo": {"full_name": "acme/api"}}, "head": {"ref": "feat/x"},
    "created_at": "2026-09-01T00:00:00Z", "merged_at": None,
    "html_url": "https://github.com/acme/api/pull/7", "author_association": "MEMBER",
    "additions": 120, "changed_files": 4, "auto_merge": None,
}


def test_only_simple_fields_survive():
    kept = simple_fields(PR_PAYLOAD, skip=("user", "state"))
    assert kept == {
        "draft": False, "locked": False, "labels": ["bug", "p1"], "milestone": "v2.0",
        "assignees": ["bo"], "base": "main", "head": "feat/x",
        "author_association": "MEMBER", "additions": 120, "changed_files": 4,
    }


@pytest.mark.parametrize("key", [
    "body", "title", "id", "node_id", "number", "html_url", "created_at", "user_email",
])
def test_bodies_ids_links_dates_and_emails_are_never_kept(key):
    assert key not in simple_fields(PR_PAYLOAD)


def test_an_email_value_is_dropped_whatever_its_key():
    assert simple_fields({"owner": "ada@acme.test", "team": "core"}) == {"team": "core"}


def test_long_text_and_links_are_dropped():
    assert simple_fields({"note": "x" * 500, "home": "https://acme.test"}) == {}


def test_the_field_count_is_bounded():
    payload = {f"f{i}": i for i in range(MAX_FIELDS + 20)}
    assert len(simple_fields(payload)) == MAX_FIELDS


def test_keys_are_normalized_to_bare_identifiers():
    assert normalize_key("projectMilestone") == "project_milestone"
    assert normalize_key("priorityLabel") == "priority_label"
    assert normalize_key("x'); DROP") == "x_drop"
    assert normalize_key("123") is None
    for key in simple_fields({"weird key!": "a", "camelCase": "b"}):
        assert KEY_RE.match(key)


def test_a_github_pr_carries_its_simple_fields():
    pull = _pull_request("acme/api", PR_PAYLOAD)
    attrs = github_facts._pull_attrs(pull).obj
    assert attrs["milestone"] == "v2.0" and attrs["labels"] == ["bug", "p1"]
    assert "body" not in attrs and "user" not in attrs and "state" not in attrs
    hash(pull)  # still a hashable value object


def test_linear_capture_rides_the_existing_feed_query():
    q = linear_source._RECENT_ISSUES_QUERY
    for field in ("priorityLabel", "estimate", "project { name }", "cycle { number name }",
                  "projectMilestone { name }", "labels(first: 10)"):
        assert field in q


def test_linear_feed_keeps_every_simple_field_in_one_call():
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
                    "priorityLabel": "High", "estimate": 3,
                    "project": {"name": "Q3"}, "labels": {"nodes": [{"name": "bug"}]},
                    "cycle": {"number": 12, "name": None},
                    "projectMilestone": {"name": "Beta"}, "slaType": None,
                }],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }}

    [issue] = linear_source.LinearAdapter.fetch_recent_issues(
        Adapter(), datetime(2026, 1, 1, tzinfo=timezone.utc)
    )
    assert len(calls) == 1
    assert issue["fields"] == {
        "priority_label": "High", "estimate": 3, "project": "Q3", "labels": ["bug"],
        "cycle": "#12", "project_milestone": "Beta",
    }
    # Plus Linear's own type for the state: it decides "open" and "closed".
    assert linear_facts._issue_attrs(issue).obj == {**issue["fields"], "state_type": "unstarted"}


def test_hints_are_bare_identifiers():
    for a in registry.ATTRS:
        assert KEY_RE.match(a.key) and a.type in registry.ATTR_TYPES, a


def test_sentiment_reads_no_attributes_even_when_offered():
    metric = registry.get("sentiment_by_theme")
    fake = (registry.Attr("team", "forms", ("sentiment",), "category", "team"),)
    assert query.readable_attrs(metric, fake) == ()


def test_a_key_that_is_not_an_identifier_is_never_offered():
    metric = registry.get("prs_merged")
    bad = (registry.Attr("x'--", "github", ("pr_merged",), "category", "x"),)
    assert query.group_dims(metric, bad) == metric.dims


# --------------------------------------------------------------------------
# Discovery and SQL -- a real database
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


def _fact(org_id, *, provider="linear", kind="issue_completed", attrs, actor="ada",
          subject="Core", external_id=None, url=None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO activity_facts
                (org_id, provider, kind, actor, subject, occurred_at,
                 external_id, url, attrs)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (org_id, provider, kind, actor, subject, datetime.now(timezone.utc),
             external_id or uuid.uuid4().hex, url, Jsonb(attrs)),
        )
        conn.commit()


@pytest.fixture
def issues(org):
    for i, attrs in enumerate([
        {"priority_label": "Urgent", "labels": ["bug", "backend"], "estimate": 3,
         "cycle": "#12", "branch": "b1"},
        {"priority_label": "Urgent", "labels": ["bug"], "estimate": 5, "cycle": "#12",
         "branch": "b2"},
        {"priority_label": "Low", "estimate": 1, "cycle": "#13", "branch": "b3"},
        {"cycle": "#13", "branch": "b4"},
        {"cycle": "#13", "branch": "b5"},
        {},  # written before the column existed
    ]):
        _fact(org, attrs=attrs)
    return org


def _found(org):
    return attr_catalog.discover_scope(org_id=org, workspace_id=None)[
        ("linear", "issue_completed")]


@requires_db
def test_discovery_types_fields_from_the_data(issues):
    found = {a.key: a for a in _found(issues)}
    assert found["priority_label"].type == "category"
    assert found["priority_label"].label == "priority"  # from the hint
    assert found["labels"].type == "tags"
    assert found["estimate"].type == "number"
    assert found["cycle"].type == "category" and found["cycle"].label == "cycle"


@requires_db
def test_identifier_like_text_is_not_offered_as_a_breakdown(issues):
    # One branch per item: a breakdown by it would be a list, not a chart.
    assert "branch" not in {a.key for a in _found(issues)}


@requires_db
def test_a_field_no_hint_mentions_is_chartable_end_to_end(issues):
    """`cycle` is in no list anywhere -- only in the data."""
    assert all(a.key != "cycle" for a in registry.ATTRS)
    got = {p.group: p.value for p in store.run_metric(
        "issues_completed", org_id=issues, workspace_id=None, period="quarter",
        group_by="cycle")}
    assert got == {"#12": 2, "#13": 3, None: 1}


@requires_db
def test_a_field_absent_from_the_scope_is_refused(issues):
    with pytest.raises(ValueError):
        store.run_metric("issues_completed", org_id=issues, workspace_id=None,
                         period="quarter", group_by="milestone")


@requires_db
def test_a_tag_counts_under_each_tag_and_keeps_untagged_items(issues):
    got = {p.group: p.value for p in store.run_metric(
        "issues_completed", org_id=issues, workspace_id=None, period="quarter",
        group_by="labels")}
    assert got == {"bug": 2, "backend": 1, None: 4}


@requires_db
def test_filters_and_number_measures(issues):
    kw = dict(org_id=issues, workspace_id=None, period="quarter")
    assert sum(p.value for p in store.run_metric(
        "issues_completed", filters=(("labels", "bug"),), **kw)) == 2
    got = {p.group: p.value for p in store.run_metric(
        "issues_completed", group_by="priority_label", measure="total_estimate", **kw)}
    assert got["Urgent"] == 8 and got["Low"] == 1


@requires_db
def test_the_hover_carries_discovered_fields_only(issues):
    attrs = _found(issues)
    rows = store.list_facts("issues_completed", org_id=issues, workspace_id=None,
                            days=90, attrs=attrs)
    for r in rows:
        assert "branch" not in r.attrs


@requires_db
def test_the_agent_titles_and_caveats_a_discovered_tag_chart(issues):
    spec = ChartSpec(metric="issues_completed", group_by="labels", period="month",
                     chart="bar", filters=(("priority_label", "urgent"),))
    panel, _ = insights_agent._run_spec(spec, org_id=issues, workspace_id=None,
                                        user_id="", role="member")
    assert panel["title"] == "Tasks completed by label — priority: Urgent"
    assert "counts under each" in panel["caveat"] and "Unknown" in panel["caveat"]


@requires_db
def test_discovery_is_scoped_and_viewer_filtered(org):
    from app.vectorstore.base import Viewer

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (org_id, title, source_uri, source_provider,
                   source_external_id, doc_is_public, doc_viewers)
               VALUES (%s, 'secret', 'https://l.test/SYV-9', 'linear', 'SYV-9', FALSE,
                       ARRAY['finance@acme.test'])""",
            (org,),
        )
        conn.commit()
    for _ in range(3):
        # Linear issue facts reach their document through the issue URL.
        _fact(org, attrs={"codename": "atlas"}, url="https://l.test/SYV-9")
    assert attr_catalog.discover_scope(org_id=org, workspace_id=None,
                                       viewer=Viewer(email="sana@acme.test")) == {}
    assert attr_catalog.discover_scope(org_id=org, workspace_id=None) != {}


@requires_db
def test_a_resync_fills_attrs_on_old_rows(org):
    issue = {"identifier": "SYV-9", "state": "Done", "state_type": "completed",
             "assignee": "Sana", "team": "Core", "url": "https://linear.test/SYV-9",
             "created_at": datetime.now(timezone.utc) - timedelta(days=2),
             "completed_at": datetime.now(timezone.utc), "at": datetime.now(timezone.utc)}
    linear_facts._write(linear_facts._issue_rows(org, None, issue), None)
    issue["fields"] = {"priority_label": "High", "labels": ["bug"]}
    linear_facts._write(linear_facts._issue_rows(org, None, issue), None)
    with get_connection() as conn:
        got = conn.execute(
            "SELECT DISTINCT attrs FROM activity_facts WHERE org_id = %s", (org,)
        ).fetchall()
    assert [r[0] for r in got] == [{"priority_label": "High", "labels": ["bug"],
                                    "state_type": "completed"}]
