"""Every chart shape, for every connector, checked against an independent count.

The other chart tests pin one rule each. This one asks the question a person
testing on staging asks -- "is the chart RIGHT?" -- for everything at once:

1. seed a known set of activity for every tool (Notion, Drive, Slack, Linear,
   GitHub, Forms), including items the asker must NOT see (a private Drive
   file, a private Slack channel, a Linear team they are not in) and items
   outside the window;
2. for every metric, run every spec the grammar admits through the real agent
   (`InsightsAgent.answer` -> `_run_spec` -> SQL): a trend per period, each
   breakdown, each breakdown + split, each measure, a filter on each field,
   the open/closed state groups, and every field DISCOVERED in the data;
3. compute what each chart should say in plain Python from the seeded list,
   and compare every non-zero bar.

What this cannot cover is the step before it: the model turning a sentence
into a spec. That needs a model, and is what the staging question list is for.
"""

from __future__ import annotations

import itertools
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from psycopg.types.json import Jsonb

from app.agent.insights_agent import InsightsAgent
from app.db import get_connection
from app.insights import attr_catalog, query, registry, scopes
from app.insights.resolve import ChartSpec
from app.vectorstore.base import Viewer
from .conftest import requires_db

pytestmark = requires_db

SANA = Viewer(email="sana@acme.test")
ACL = {"google", "slack", "linear"}  # = sources.factory.ACL_CAPABLE
FINISHED = {"completed", "canceled"}  # = store.FINISHED_STATE_TYPES

ACTORS = ("ada", "bob", "cy")


@dataclass
class F:
    provider: str
    kind: str
    actor: str
    subject: str
    days_ago: int
    state: str | None = None
    value: float | None = None
    attrs: dict = field(default_factory=dict)
    visible: bool = True


def _facts() -> list[F]:
    """A deterministic spread: three people, two subjects each, days 0..60,
    every tenth item private, a few beyond the shortest window."""
    out: list[F] = []
    for i in range(40):
        actor = ACTORS[i % 3]
        ago = (i * 7) % 61
        private = i % 10 == 9
        out.append(F("notion", "doc_changed", actor, ("Roadmap", "Handbook")[i % 2], ago))
        out.append(F("google", "doc_changed", actor, ("Budget", "Plan")[i % 2], ago,
                     visible=not private))
        out.append(F("slack", "doc_changed", actor, ("#eng", "#sales")[i % 2], ago,
                     visible=not private))
        state, kind = [("Todo", "unstarted"), ("In Progress", "started"),
                       ("Done", "completed"), ("Canceled", "canceled")][i % 4]
        issue_attrs = {"state_type": kind, "priority": ("High", "Low")[i % 2],
                       "estimate": i % 5 + 1, "labels": [("bug", "ui"), ("api",)][i % 2]}
        out.append(F("linear", "issue_state", actor, ("CORE", "GROW")[i % 2], ago,
                     state=state, attrs=issue_attrs, visible=not private))
        if kind == "completed":
            out.append(F("linear", "issue_completed", actor, ("CORE", "GROW")[i % 2], ago,
                         state=state, value=(i + 1) * 3600.0, attrs=issue_attrs,
                         visible=not private))
        pr_attrs = {"additions": i * 10, "deletions": i, "changed_files": i % 4 + 1,
                    "labels": [("feature",), ("fix", "infra")][i % 2]}
        repo = ("acme/api", "acme/web")[i % 2]
        out.append(F("github", "pr_opened", actor, repo, ago,
                     state=("open", "closed")[i % 2], attrs=pr_attrs))
        if i % 2:
            out.append(F("github", "pr_merged", actor, repo, ago,
                         value=(i + 2) * 86400.0 / 4, attrs=pr_attrs))
        out.append(F("github", "pr_reviewed", ACTORS[(i + 1) % 3], repo, ago))
        out.append(F("github", "commit", actor, repo, ago))
    return out


@pytest.fixture(scope="module")
def seeded():
    """One org holding every fact above; dropped at the end."""
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    facts = _facts()
    with get_connection() as conn:
        org = str(conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"e2e-{uuid.uuid4().hex[:8]}",),
        ).fetchone()[0])
        tz = ZoneInfo(conn.execute("SHOW TimeZone").fetchone()[0])
        for f in facts:
            ext = uuid.uuid4().hex
            url = f"https://{f.provider}.test/{ext}"
            if f.provider in ACL:
                # The document the viewer filter checks: public, or shared
                # only with someone else.
                conn.execute(
                    "INSERT INTO documents (org_id, title, source_uri, source_provider, "
                    "source_external_id, doc_is_public, doc_viewers) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (org, f.subject, url, f.provider, ext, f.visible,
                     [] if f.visible else ["finance@acme.test"]),
                )
            conn.execute(
                "INSERT INTO activity_facts (org_id, provider, kind, actor, subject, state, "
                "occurred_at, value, url, external_id, attrs) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (org, f.provider, f.kind, f.actor, f.subject, f.state,
                 now - timedelta(days=f.days_ago), f.value, url, ext, Jsonb(f.attrs)),
            )
        conn.commit()
    yield {"org": org, "facts": facts, "now": now, "tz": tz}
    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s", (org,))
        conn.commit()


# --------------------------------------------------------------------------
# The independent count
# --------------------------------------------------------------------------


def _bucket(when: datetime, period: str, tz) -> str:
    d = when.astimezone(tz).date()
    if period == "week":
        d = d - timedelta(days=d.weekday())
    elif period == "month":
        d = d.replace(day=1)
    elif period == "quarter":
        d = date(d.year, (d.month - 1) // 3 * 3 + 1, 1)
    return d.isoformat()


def _values(f: F, dim: str | None) -> list:
    if dim is None:
        return [None]
    if dim in ("actor", "subject", "state"):
        return [getattr(f, dim)]
    v = f.attrs.get(dim)
    if isinstance(v, (list, tuple)):
        return list(v)
    return [None if v is None else str(v)]


def _aggregate(metric, measure: str, rows: list[F]) -> float:
    if measure == "count":
        return float(len(rows))
    if measure == "people":
        return float(len({r.actor for r in rows if r.actor}))
    if measure == "subjects":
        return float(len({r.subject for r in rows if r.subject}))
    if measure in ("median", "average", "longest"):
        days = [r.value / 86400.0 for r in rows if r.value is not None]
        return {"median": statistics.median, "average": statistics.fmean,
                "longest": max}[measure](days)
    agg, _, key = measure.partition("_")
    nums = [float(r.attrs[key]) for r in rows if r.attrs.get(key) is not None]
    return sum(nums) if agg == "total" else statistics.fmean(nums)


def _passes(f: F, filters, exact_states=frozenset()) -> bool:
    for dim, value in filters:
        # A state literally called "open"/"closed" (GitHub's) wins first;
        # otherwise the two tokens are the source's unfinished/finished group.
        if dim == "state" and value in ("open", "closed") and value not in exact_states:
            done = f.attrs.get("state_type") in FINISHED
            if done != (value == "closed"):
                return False
        elif value not in _values(f, dim):
            return False
    return True


def expected(seeded, metric, *, period, group_by=None, split_by=None,
             measure=None, filters=()) -> dict:
    measure = measure or query.default_measure(metric)
    days = scopes.WINDOW_DAYS[period]
    since = seeded["now"] - timedelta(days=days)
    exact = frozenset(f.state for f in seeded["facts"]
                      if f.provider == metric.provider and f.kind == metric.kind)
    rows = [f for f in seeded["facts"]
            if f.provider == metric.provider and f.kind == metric.kind and f.visible
            and seeded["now"] - timedelta(days=f.days_ago) >= since
            and _passes(f, filters, exact)]
    groups: dict[tuple, list[F]] = {}
    for f in rows:
        when = seeded["now"] - timedelta(days=f.days_ago)
        for g, s in itertools.product(_values(f, group_by), _values(f, split_by)):
            groups.setdefault((_bucket(when, period, seeded["tz"]), g, s), []).append(f)
    out = {k: _aggregate(metric, measure, v) for k, v in groups.items()}
    return {k: round(v, 4) for k, v in out.items() if round(v, 4) != 0}


def actual(seeded, spec: ChartSpec) -> dict:
    response = InsightsAgent().answer(
        "e2e", seeded["org"], viewer=SANA, spec=spec,
        user_id=str(uuid.uuid4()), role="owner",
    )
    assert response.chart is not None, f"{spec}: no chart -- {response.answer}"
    out = {}
    for p in response.chart["points"]:
        if round(p["value"], 4) != 0:
            out[(p["bucket"][:10], p["group"], p["series"])] = round(p["value"], 4)
    return out


# --------------------------------------------------------------------------
# Every spec the grammar admits, per metric
# --------------------------------------------------------------------------


def _fields(seeded, metric) -> list[str]:
    attrs = attr_catalog.for_metric(metric, org_id=seeded["org"], workspace_id=None,
                                    viewer=SANA)
    return [a.key for a in attrs if a.type in ("category", "tags")
            and a.key != "state_type"]


def _admitted(metric, attrs, *, group_by=None, split_by=None, measure=None,
              filters=()) -> bool:
    try:
        query.validate(metric, group_by=group_by, split_by=split_by, measure=measure,
                       filters=filters, attrs=attrs)
        return True
    except ValueError:
        return False


def _specs(seeded, metric):
    attrs = attr_catalog.for_metric(metric, org_id=seeded["org"], workspace_id=None,
                                    viewer=SANA)
    dims = list(metric.dims) + _fields(seeded, metric)
    chart = metric.chart
    for period in ("week", "month"):
        yield dict(period=period)
    for g in dims:
        if _admitted(metric, attrs, group_by=g):
            yield dict(period="month", group_by=g)
    for g, s in itertools.permutations(dims, 2):
        if _admitted(metric, attrs, group_by=g, split_by=s):
            yield dict(period="month", group_by=g, split_by=s)
    for m in query.measures_for(metric, attrs):
        if _admitted(metric, attrs, group_by=dims[0], measure=m):
            yield dict(period="month", group_by=dims[0], measure=m)
    sample = {"actor": "ada", "subject": None, "state": None}
    for dim in dims:
        values = sorted({v for f in seeded["facts"]
                         if f.provider == metric.provider and f.kind == metric.kind
                         for v in _values(f, dim) if v is not None})
        value = sample.get(dim) or (values[0] if values else None)
        other = next((d for d in dims if d != dim), None)
        if value and _admitted(metric, attrs, group_by=other, filters=((dim, value),)):
            yield dict(period="month", group_by=other, filters=((dim, value),))
    if metric.key == "issue_states":
        for token in ("open", "closed"):
            yield dict(period="month", group_by="actor", filters=(("state", token),))
    del chart


METRICS = [m for m in registry.METRICS.values() if m.key != "sentiment_by_theme"]


@pytest.mark.parametrize("metric", METRICS, ids=lambda m: m.key)
def test_every_chart_shape_matches_an_independent_count(seeded, metric):
    ran = 0
    failures = []
    for kw in _specs(seeded, metric):
        spec = ChartSpec(metric=metric.key, period=kw["period"], chart=metric.chart,
                         group_by=kw.get("group_by"), split_by=kw.get("split_by"),
                         measure=kw.get("measure"), filters=kw.get("filters", ()))
        want = expected(seeded, metric, **kw)
        got = actual(seeded, spec)
        ran += 1
        if got != want:
            missing = {k: v for k, v in want.items() if got.get(k) != v}
            extra = {k: v for k, v in got.items() if want.get(k) != v}
            failures.append(f"{kw}: expected {missing} got {extra}")
    assert ran >= 4, f"only {ran} shapes ran for {metric.key}"
    assert not failures, "\n".join(failures)


def test_open_among_completed_tasks_is_refused_naming_the_states(seeded):
    """Every completed task is finished, so "open" has nothing to mean:
    refused by name rather than drawn as an empty chart."""
    response = InsightsAgent().answer(
        "e2e", seeded["org"], viewer=SANA, user_id="u", role="member",
        spec=ChartSpec(metric="issues_completed", group_by="actor", period="month",
                       chart="line", filters=(("state", "open"),)),
    )
    assert response.chart is None and "Done" in response.answer


def test_private_items_are_never_counted(seeded):
    metric = registry.get("drive_docs_changed")
    got = actual(seeded, ChartSpec(metric=metric.key, group_by=None, period="month", chart="line"))
    hidden = sum(1 for f in seeded["facts"]
                 if f.provider == "google" and not f.visible)
    shown = sum(1 for f in seeded["facts"] if f.provider == "google" and f.visible)
    assert hidden and sum(got.values()) == shown


def test_a_trend_has_every_period_from_its_first_item(seeded):
    response = InsightsAgent().answer(
        "e2e", seeded["org"], viewer=SANA, user_id="u", role="owner",
        spec=ChartSpec(metric="commits_by_author", group_by=None, period="week", chart="line"),
    )
    buckets = [p["bucket"][:10] for p in response.chart["points"]]
    weeks = [date.fromisoformat(b) for b in buckets]
    assert all((b - a).days == 7 for a, b in zip(weeks, weeks[1:])), buckets


def test_forms_sentiment_keeps_its_floor(seeded):
    """Owners only, floor of 5 per theme: a theme with fewer responses is
    never drawn, however it is split."""
    now = seeded["now"]
    with get_connection() as conn:
        for theme, n in (("Pay", 6), ("Office", 2)):
            for i in range(n):
                conn.execute(
                    "INSERT INTO activity_facts (org_id, provider, kind, subject, state, "
                    "occurred_at, external_id) VALUES (%s, 'forms', 'sentiment', %s, %s, %s, %s)",
                    (seeded["org"], theme, ("positive", "negative")[i % 2],
                     now - timedelta(days=i), uuid.uuid4().hex),
                )
        conn.commit()
    response = InsightsAgent().answer(
        "e2e", seeded["org"], viewer=SANA, user_id="u", role="admin",
        spec=ChartSpec(metric="sentiment_by_theme", group_by="subject",
                       period="quarter", chart="diverging_bar"),
    )
    assert response.chart is not None, response.answer
    groups = {p["group"] for p in response.chart["points"]}
    assert groups == {"Pay"}
    member = InsightsAgent().answer(
        "e2e", seeded["org"], viewer=SANA, user_id="u", role="member",
        spec=ChartSpec(metric="sentiment_by_theme", group_by="subject",
                       period="quarter", chart="diverging_bar"),
    )
    assert member.chart is None


def test_an_unassigned_linear_issue_is_unassigned_not_unknown(seeded):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO activity_facts (org_id, provider, kind, actor, subject, state, "
            "occurred_at, external_id, attrs) VALUES (%s, 'linear', 'issue_state', NULL, "
            "'CORE', 'Todo', %s, %s, %s)",
            (seeded["org"], seeded["now"], uuid.uuid4().hex,
             Jsonb({"state_type": "unstarted"})),
        )
        conn.commit()
    response = InsightsAgent().answer(
        "e2e", seeded["org"], viewer=Viewer.unrestricted(), user_id="u", role="member",
        spec=ChartSpec(metric="issue_states", group_by="actor", period="month", chart="bar"),
    )
    assert response.chart["blank_labels"] == {"actor": "Unassigned"}
    assert "by assignee" in response.chart["title"]


def test_a_left_out_breakdown_is_said_on_the_chart(seeded):
    response = InsightsAgent().answer(
        "e2e", seeded["org"], viewer=SANA, user_id="u", role="member",
        spec=ChartSpec(metric="issues_completed", group_by="subject", split_by="actor",
                       period="month", chart="bar", left_out="and priority"),
    )
    assert response.chart["caveat"].startswith(
        'A chart shows at most two breakdowns, so "and priority" was left out.')


def test_all_unassigned_says_so_in_linears_terms():
    from app.agent.insights_agent import _caption

    caption = _caption(
        ChartSpec(metric="issue_states", group_by="actor", period="month", chart="bar"),
        {"title": "Where the work sits by assignee"}, [{"group": None, "value": 7}],
        metric=registry.get("issue_states"),
    )
    assert "Unassigned" in caption and "indexed" not in caption
