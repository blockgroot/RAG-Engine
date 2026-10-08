"""The chart query grammar (`app/insights/query.py`).

Step 1 of docs/plans/2026-09-30-open-ended-charts.md: the model may compose a
chart -- a measure, a second grouping, a person/state filter -- over one
registry metric, instead of only picking a finished chart. Pinned here:

- every slot is a CLOSED set per metric, validated in the resolver AND the
  store, and refused with the options rather than silently corrected;
- a protected metric (survey sentiment) gets none of it;
- filter values are resolved against real rows and BOUND, never spliced;
- the hover rows narrow exactly as the bars do;
- a spec survives the trip through the agent graph's dict state, ``focus``
  included (chat and Slack used to drop it).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from app.agent import insights_agent
from app.db import get_connection
from app.insights import query, registry, resolve, store
from app.insights.resolve import CannotChart, ChartSpec
from .conftest import requires_db


class FakeLLM:
    model = "test-model"
    last_usage = None

    def __init__(self, reply):
        self._reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt, *, max_tokens=None):
        self.prompts.append(prompt)
        return self._reply


def _classify(reply: dict, providers=("github",), question="chart PRs merged by person"):
    return resolve.classify_question(
        question, providers=list(providers), llm=FakeLLM(json.dumps(reply)),
        fail_open=False,
    )


# --------------------------------------------------------------------------
# The grammar itself -- no DB
# --------------------------------------------------------------------------


def test_the_default_measure_is_the_metrics_own_select():
    """An old spec must chart exactly what it always did."""
    for metric in registry.METRICS.values():
        measures = query.measures_for(metric)
        first = next(iter(measures.values()))
        assert first.select is None
        assert first.unit == metric.unit
        assert query.default_measure(metric) == first.name


def test_a_protected_metric_admits_no_slicing():
    """Sentiment: a split multiplies buckets past the floor, a person filter
    points it at one colleague, and a distinct count is a respondent tally."""
    metric = registry.get("sentiment_by_theme")
    assert query.is_protected(metric)
    assert list(query.measures_for(metric)) == ["count"]
    assert query.split_dims(metric) == ()
    assert query.filter_dims(metric) == ()
    with pytest.raises(ValueError):
        query.validate(metric, group_by="subject", split_by="state", measure=None)
    with pytest.raises(ValueError):
        query.validate(metric, group_by=None, split_by=None, measure=None,
                       filters=(("actor", "someone"),))


def test_duration_measures_only_apply_to_seconds_metrics_in_days():
    """`_DURATION_MEASURES` divides by 86400 -- right only for a value stored
    in seconds and shown in days. A future value metric in another unit must
    fail here rather than inherit the wrong conversion."""
    for metric in registry.METRICS.values():
        if query.is_duration(metric) and not query.is_protected(metric):
            assert "/ 86400.0" in metric.select, metric.key
            assert metric.unit.startswith("days"), metric.key
            assert {"average", "longest"} <= set(query.measures_for(metric))


def test_every_measure_fragment_is_a_constant_without_format_holes():
    for metric in registry.METRICS.values():
        for measure in query.measures_for(metric).values():
            if measure.select:
                assert not any(c in measure.select for c in "{};%"), measure


def test_validate_refuses_rather_than_corrects():
    metric = registry.get("prs_merged")
    with pytest.raises(ValueError, match="Options"):
        query.validate(metric, group_by="actor", split_by=None, measure="average")
    with pytest.raises(ValueError, match="needs a first"):
        query.validate(metric, group_by=None, split_by="subject", measure=None)
    with pytest.raises(ValueError, match="grouped by"):
        query.validate(metric, group_by="actor", split_by="actor", measure=None)
    with pytest.raises(ValueError, match="narrowed"):
        # `subject` is focus's job, not a filter's.
        query.validate(metric, group_by=None, split_by=None, measure=None,
                       filters=(("subject", "api"),))
    query.validate(metric, group_by="actor", split_by="subject", measure="people",
                   filters=(("actor", "sana"),))


def test_store_revalidates_so_no_call_site_can_bypass_the_grammar():
    with pytest.raises(ValueError):
        store.run_metric("sentiment_by_theme", org_id=str(uuid.uuid4()),
                         workspace_id=None, period="month",
                         group_by="subject", split_by="state")
    with pytest.raises(ValueError):
        store.run_metric("prs_merged", org_id=str(uuid.uuid4()),
                         workspace_id=None, period="month", measure="DROP")


# --------------------------------------------------------------------------
# The resolver
# --------------------------------------------------------------------------


def test_the_resolver_reads_all_three_slots():
    intent = _classify({
        "intent": "chart", "metric": "prs_merged", "group_by": "actor",
        "split_by": "subject", "measure": "count", "period": "week",
        "filters": {"actor": "Sana"},
    })
    assert intent.kind == "chart"
    spec = intent.spec
    assert (spec.group_by, spec.split_by) == ("actor", "subject")
    assert spec.measure is None  # the default is stored as "not set"
    assert spec.filters == (("actor", "Sana"),)


def test_an_unknown_measure_is_a_refusal_naming_the_options():
    intent = _classify({"intent": "chart", "metric": "prs_merged",
                        "group_by": "actor", "measure": "salary", "period": "month"})
    assert intent.kind == "refuse"
    assert "people" in intent.message


def test_a_split_in_the_wrong_slot_becomes_the_grouping():
    intent = _classify({"intent": "chart", "metric": "prs_merged", "group_by": None,
                        "split_by": "subject", "period": "month",
                        "breakdown_words": "by person"})
    assert intent.kind == "chart"
    assert (intent.spec.group_by, intent.spec.split_by) == ("subject", None)


def test_a_misplaced_split_outside_the_dims_is_still_refused():
    intent = _classify({"intent": "chart", "metric": "pr_lead_time",
                        "group_by": None, "split_by": "actor", "period": "month"})
    assert intent.kind == "refuse"


def test_the_catalogue_offers_slots_only_where_they_apply():
    llm = FakeLLM(json.dumps({"intent": "qa"}))
    resolve.classify_question("chart sentiment", providers=["forms", "github"],
                              llm=llm, fail_open=False)
    prompt = llm.prompts[0]
    sentiment = next(l for l in prompt.splitlines() if l.startswith("- sentiment_by_theme"))
    assert "measure:" not in sentiment and "split_by:" not in sentiment
    assert "filters:" not in sentiment
    merged = next(l for l in prompt.splitlines() if l.startswith("- prs_merged"))
    assert "measure: count," in merged and "people, subjects" in merged
    assert "split_by: actor, subject" in merged


def test_patching_away_the_grouping_drops_the_split():
    spec = ChartSpec(metric="prs_merged", group_by="actor", period="month",
                     chart="bar", split_by="subject")
    assert resolve.patch_spec(spec, group_by=None).split_by is None
    assert resolve.patch_spec(spec, group_by="subject").split_by is None


def test_a_spec_survives_the_agent_graph_state_focus_included():
    """Chat and Slack built this dict by hand and dropped `focus`, so
    "commits in the DAO repo" charted every repository."""
    spec = ChartSpec(metric="prs_merged", group_by="actor", period="week",
                     chart="bar", focus="DAO", split_by="subject",
                     measure="people", filters=(("actor", "Sana"),))
    data = json.loads(json.dumps(resolve.spec_to_dict(spec)))  # as graph state
    assert resolve.spec_from_dict(data) == spec
    assert insights_agent._as_spec(data) == spec
    # The older four-key shape still loads.
    old = {"metric": "prs_merged", "group_by": None, "period": "month", "chart": "line"}
    assert resolve.spec_from_dict(old).filters == ()


# --------------------------------------------------------------------------
# SQL -- a real database
# --------------------------------------------------------------------------


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"query-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _merged(org_id, actor, repo, *, state=None, url=None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO activity_facts
                (org_id, provider, kind, actor, subject, state, occurred_at,
                 url, external_id)
            VALUES (%s, 'github', 'pr_merged', %s, %s, %s, %s, %s, %s)
            """,
            (org_id, actor, repo, state, datetime.now(timezone.utc), url,
             uuid.uuid4().hex),
        )
        conn.commit()


@pytest.fixture
def prs(org):
    for actor, repo in [
        ("18-sana", "acme/api"), ("18-sana", "acme/api"), ("18-sana", "acme/web"),
        ("rahul-k", "acme/api"),
    ]:
        _merged(org, actor, repo, url=f"https://github.test/{actor}/{repo}")
    return org


@requires_db
def test_a_split_groups_by_both_dimensions(prs):
    points = store.run_metric("prs_merged", org_id=prs, workspace_id=None,
                              period="quarter", group_by="actor", split_by="subject")
    got = {(p.group, p.series): p.value for p in points}
    assert got == {("18-sana", "acme/api"): 2, ("18-sana", "acme/web"): 1,
                   ("rahul-k", "acme/api"): 1}


@requires_db
def test_the_people_measure_counts_distinct_people(prs):
    points = store.run_metric("prs_merged", org_id=prs, workspace_id=None,
                              period="quarter", group_by="subject", measure="people")
    assert {p.group: p.value for p in points} == {"acme/api": 2, "acme/web": 1}


@requires_db
def test_a_filter_narrows_the_bars_and_the_hover_rows_alike(prs):
    points = store.run_metric("prs_merged", org_id=prs, workspace_id=None,
                              period="quarter", group_by="subject",
                              filters=(("actor", "rahul-k"),))
    assert {p.group: p.value for p in points} == {"acme/api": 1}
    rows = store.list_facts("prs_merged", org_id=prs, workspace_id=None, days=90,
                            filters=(("actor", "rahul-k"),))
    assert [r.actor for r in rows] == ["rahul-k"]


@requires_db
def test_a_filter_value_is_bound_never_spliced(prs):
    evil = "x' OR '1'='1"
    points = store.run_metric("prs_merged", org_id=prs, workspace_id=None,
                              period="quarter", filters=(("actor", evil),))
    assert points == []


@requires_db
def test_list_values_lists_what_has_rows(prs):
    assert store.list_values("prs_merged", "actor", org_id=prs, workspace_id=None,
                             days=90) == ["18-sana", "rahul-k"]
    with pytest.raises(ValueError):
        store.list_values("prs_merged", "salary", org_id=prs, workspace_id=None, days=90)


@requires_db
def test_the_agent_resolves_a_typed_name_and_titles_every_slice(prs):
    spec = ChartSpec(metric="prs_merged", group_by="subject", period="month",
                     chart="bar", measure="people", filters=(("actor", "Sana"),))
    panel, _ = insights_agent._run_spec(spec, org_id=prs, workspace_id=None,
                                        user_id="", role="member")
    assert panel["title"] == "People — pull requests merged by repository — person: 18-sana"
    assert panel["unit"] == "people"
    assert {p["group"]: p["value"] for p in panel["points"]} == {"acme/api": 1, "acme/web": 1}
    assert {d["actor"] for d in panel["details"]} == {"18-sana"}


@requires_db
def test_an_unknown_name_is_refused_by_name_listing_who_is_there(prs):
    spec = ChartSpec(metric="prs_merged", group_by=None, period="month",
                     chart="line", filters=(("actor", "priya"),))
    with pytest.raises(CannotChart) as err:
        insights_agent._run_spec(spec, org_id=prs, workspace_id=None,
                                 user_id="", role="member")
    assert "priya" in str(err.value) and "18-sana" in str(err.value)


@requires_db
def test_the_split_reaches_the_panel(prs):
    spec = ChartSpec(metric="prs_merged", group_by="actor", period="month",
                     chart="bar", split_by="subject")
    panel, _ = insights_agent._run_spec(spec, org_id=prs, workspace_id=None,
                                        user_id="", role="member")
    assert panel["split_by"] == "subject"
    assert panel["title"] == "Pull requests merged by person and repository"
    assert all(p["series"] for p in panel["points"])


@requires_db
def test_a_value_standing_for_several_matches_any_of_them(prs):
    """"Open issues" is several states; the filter matches each, bound."""
    from app.insights.query import AnyOf

    both = AnyOf("Team", ("18-sana", "rahul-k"))
    points = store.run_metric("prs_merged", org_id=prs, workspace_id=None,
                              period="quarter", group_by="actor", filters=(("actor", both),))
    assert {p.group: p.value for p in points} == {"18-sana": 3, "rahul-k": 1}
    only = AnyOf("Rahul", ("rahul-k", "x' OR '1'='1"))
    points = store.run_metric("prs_merged", org_id=prs, workspace_id=None,
                              period="quarter", group_by="actor", filters=(("actor", only),))
    assert {p.group: p.value for p in points} == {"rahul-k": 1}


LINEAR_KINDS = {"Backlog": "backlog", "Todo": "unstarted", "In Progress": "started",
                "Shipped": "completed", "Won't do": "canceled"}


@pytest.mark.parametrize("word, expected", [
    ("open", ("Backlog", "Todo", "In Progress")),
    ("closed", ("Shipped", "Won't do")),
])
def test_open_and_closed_follow_the_sources_own_state_types(monkeypatch, word, expected):
    """No word list decides "finished": Linear's type does, so a custom
    "Shipped" is closed because Linear calls it completed."""
    from types import SimpleNamespace

    from app.agent import insights_agent

    monkeypatch.setattr(insights_agent.store, "list_values", lambda *a, **k: list(LINEAR_KINDS))
    monkeypatch.setattr(insights_agent.store, "state_types", lambda *a, **k: dict(LINEAR_KINDS))
    value = insights_agent._resolve_value("state", word, SimpleNamespace(metric="issue_states"),
                                          None, org_id="o", workspace_id=None, days=365)
    assert tuple(value.values) == expected
    assert str(value) == word.capitalize()


def test_with_no_state_types_open_is_refused_not_guessed(monkeypatch):
    """No name list decides "finished": without the source's types the
    filter is refused, naming the real states."""
    from types import SimpleNamespace

    from app.agent import insights_agent
    from app.insights import registry

    monkeypatch.setattr(insights_agent.store, "list_values", lambda *a, **k: ["Review", "Done"])
    monkeypatch.setattr(insights_agent.store, "state_types",
                        lambda *a, **k: {"Review": None, "Done": None})
    with pytest.raises(insights_agent.CannotChart, match="No state called"):
        insights_agent._resolve_value("state", "open", SimpleNamespace(metric="issue_states"),
                                      registry.get("issue_states"), org_id="o",
                                      workspace_id=None, days=365)

def test_a_state_named_open_is_matched_exactly(monkeypatch):
    from types import SimpleNamespace

    from app.agent import insights_agent

    monkeypatch.setattr(insights_agent.store, "list_values", lambda *a, **k: ["Open", "Done"])
    value = insights_agent._resolve_value("state", "open", SimpleNamespace(metric="m"), None,
                                          org_id="o", workspace_id=None, days=365)
    assert value == "Open" and not hasattr(value, "values")


@requires_db
def test_the_state_type_is_stored_and_read_back(org):
    """Written at sync from Linear's own type; read per state for open/closed."""
    from app.insights import linear_facts

    issue = {"identifier": "SYV-9", "state": "Shipped", "state_type": "completed",
             "team": "Core", "assignee": "Sana", "at": datetime.now(timezone.utc),
             "created_at": datetime.now(timezone.utc), "completed_at": None,
             "url": "https://linear.test/SYV-9", "fields": {"priority_label": "High"}}
    linear_facts._write(linear_facts._issue_rows(org, None, issue), None)
    assert store.state_types("issue_states", org_id=org, workspace_id=None, days=30) == {
        "Shipped": "completed"}


def _pt(bucket, value, group=None):
    return store.Point(bucket=bucket, group=group, series=None, value=value)


def test_a_trend_shows_its_whole_range_with_quiet_weeks_at_zero():
    """One active week drew as a lone column with nothing around it."""
    from app.agent.insights_agent import _fill_gaps

    since = datetime(2026, 9, 10, tzinfo=timezone.utc)
    until = datetime(2026, 10, 8, tzinfo=timezone.utc)
    got = _fill_gaps([_pt("2026-09-21T00:00:00+00:00", 3), _pt("2026-10-05T00:00:00+00:00", 1)],
                     "week", since, until)
    assert [(p.bucket[:10], p.value) for p in got] == [
        ("2026-09-07", 0), ("2026-09-14", 0), ("2026-09-21", 3),
        ("2026-09-28", 0), ("2026-10-05", 1)]


def test_nothing_is_drawn_before_the_data_begins():
    from app.agent.insights_agent import _fill_gaps

    got = _fill_gaps([_pt("2026-09-28T00:00:00+00:00", 1)], "week",
                     datetime(2026, 10, 1, tzinfo=timezone.utc),
                     datetime(2026, 10, 8, tzinfo=timezone.utc))
    assert [p.bucket[:10] for p in got] == ["2026-09-28", "2026-10-05"]


def test_filled_buckets_line_up_with_the_databases_zone():
    from app.agent.insights_agent import _fill_gaps

    got = _fill_gaps([_pt("2026-09-28T00:00:00+05:30", 1)], "week",
                     datetime(2026, 9, 29, tzinfo=timezone.utc),
                     datetime(2026, 10, 8, tzinfo=timezone.utc))
    assert [p.bucket for p in got] == ["2026-09-28T00:00:00+05:30", "2026-10-05T00:00:00+05:30"]


def test_a_huge_daily_range_is_left_alone():
    from app.agent.insights_agent import MAX_FILLED_BUCKETS, _fill_gaps

    pts = [_pt("2026-10-01T00:00:00+00:00", 1)]
    assert _fill_gaps(pts, "day", datetime(2026, 1, 1, tzinfo=timezone.utc),
                      datetime(2026, 10, 8, tzinfo=timezone.utc)) == pts
    assert MAX_FILLED_BUCKETS == 60
