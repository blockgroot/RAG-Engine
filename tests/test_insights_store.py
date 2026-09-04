"""Charts are a tenant read, so they answer to the same isolation rule as
retrieval: ``org_id`` and ``workspace_id`` are a WHERE clause on every query.

A missing predicate here LEAKS rather than fails -- the chart still renders,
just with another company's counts in it -- so both directions are pinned: a
space must not see org-wide rows, AND the org scope must not see a space's.
Testing only one direction would let a predicate that matches everything pass.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import get_connection
from app.insights import registry, store
from .conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def org(org_cleanup):
    """A throwaway organization, cleaned up by the shared fixture."""
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"insights-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


@pytest.fixture
def space(org):
    """A sub-workspace inside ``org``. Cascades away with the org."""
    with get_connection() as conn:
        user = conn.execute(
            "INSERT INTO users (email, org_id, role) VALUES (%s, %s, 'member') RETURNING id",
            (f"{uuid.uuid4().hex[:8]}@example.com", org),
        ).fetchone()
        row = conn.execute(
            "INSERT INTO workspaces (org_id, name, created_by) VALUES (%s, %s, %s) RETURNING id",
            (org, "Meeting notes", user[0]),
        ).fetchone()
        conn.commit()
    return str(row[0])


def _fact(org_id, *, workspace_id=None, provider="notion", kind="doc_changed",
          actor=None, subject="a page", when=None, external_id=None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO activity_facts
                (org_id, workspace_id, provider, kind, actor, subject,
                 occurred_at, external_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (org_id, workspace_id, provider, kind, actor, subject,
             when or datetime.now(timezone.utc),
             external_id or uuid.uuid4().hex),
        )
        conn.commit()


def _total(rows) -> float:
    return sum(r.value for r in rows)


def test_a_metric_counts_only_the_asking_org(org, org_cleanup):
    with get_connection() as conn:
        other = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"other-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(other[0]))

    _fact(org)
    _fact(org)
    _fact(str(other[0]))

    rows = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                            period="month", days=90)
    assert _total(rows) == 2, "the other org's row must be invisible"


def test_a_space_sees_only_its_own_rows(org, space):
    """Never also org-wide ones -- a meeting-notes space blending in HR policy
    is exactly what makes membership meaningless."""
    _fact(org, workspace_id=None)
    _fact(org, workspace_id=space)

    rows = store.run_metric("docs_changed", org_id=org, workspace_id=space,
                            period="month", days=90)
    assert _total(rows) == 1


def test_the_org_scope_excludes_a_space_s_rows(org, space):
    """The mirror direction. Without it, a predicate matching everything would
    still pass the test above."""
    _fact(org, workspace_id=None)
    _fact(org, workspace_id=space)

    rows = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                            period="month", days=90)
    assert _total(rows) == 1


def test_another_providers_facts_are_not_counted(org):
    """Drive and Notion share the `doc_changed` kind, so provider is the only
    thing separating them -- a dropped predicate would double every count."""
    _fact(org, provider="notion")
    _fact(org, provider="google")

    rows = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                            period="month", days=90)
    assert _total(rows) == 1


def test_rows_outside_the_window_are_excluded(org):
    _fact(org, when=datetime.now(timezone.utc))
    _fact(org, when=datetime.now(timezone.utc) - timedelta(days=400))

    rows = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                            period="month", days=90)
    assert _total(rows) == 1


def test_grouping_splits_the_series_by_actor(org):
    """"Top editors" is this metric grouped by actor -- the grouping is where
    the leaderboard comes from, not a second metric definition."""
    _fact(org, actor="ada")
    _fact(org, actor="ada")
    _fact(org, actor="grace")

    rows = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                            period="month", days=90, group_by="actor")
    by_actor = {r.group: r.value for r in rows}
    assert by_actor == {"ada": 2.0, "grace": 1.0}


def test_an_unknown_period_is_refused_not_interpolated(org):
    """date_trunc's unit is a literal, not a bindable parameter, so a
    caller-supplied one is an injection. It must raise, not sanitize."""
    with pytest.raises(ValueError):
        store.run_metric("docs_changed", org_id=org, workspace_id=None,
                         period="week'); DROP TABLE activity_facts; --", days=30)


def test_an_unknown_dimension_is_refused(org):
    with pytest.raises(ValueError):
        store.run_metric("docs_changed", org_id=org, workspace_id=None,
                         period="week", days=30, group_by="; DROP TABLE x")


def test_an_unknown_metric_raises_rather_than_returning_nothing(org):
    """An empty chart and a nonexistent metric must not look the same -- one is
    "no activity", the other is a bug or a hallucinated key."""
    with pytest.raises(KeyError):
        store.run_metric("not_a_metric", org_id=org, workspace_id=None,
                         period="week", days=30)


def test_an_empty_result_is_empty_not_a_fabricated_zero_series(org):
    """A chart with no data must render "nothing yet", which the frontend can
    only tell apart from real zeroes if we return no rows at all."""
    rows = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                            period="month", days=30)
    assert rows == []


def test_first_fact_at_reports_when_measurement_began(org):
    """Authors and PRs are only recorded from the first sync after deploy, so a
    chart that silently starts on deploy day reads as if nobody worked before
    it. The frontend needs this date to say "measured since"."""
    assert store.first_fact_at("notion", org_id=org, workspace_id=None) is None

    when = datetime.now(timezone.utc) - timedelta(days=3)
    _fact(org, when=when)

    began = store.first_fact_at("notion", org_id=org, workspace_id=None)
    assert began is not None
    assert abs((began - when).total_seconds()) < 5


# ---------------------------------------------------------------------------
# The suppression floor. Not a display nicety -- it is what makes an anonymous
# survey anonymous, so it lives in SQL where no call site can skip it.
# ---------------------------------------------------------------------------


def _sentiment(org_id, *, theme, label, n):
    for _ in range(n):
        _fact(org_id, provider="forms", kind="sentiment", subject=theme,
              external_id=uuid.uuid4().hex)
        with get_connection() as conn:
            conn.execute(
                "UPDATE activity_facts SET state = %s "
                " WHERE org_id = %s AND state IS NULL AND provider = 'forms'",
                (label, org_id),
            )
            conn.commit()


def test_a_group_below_the_floor_never_leaves_the_database(org):
    """On a six-person team, "3 of 4 responses in Engineering are negative"
    identifies people. Anyone who knows the team can work out which."""
    _sentiment(org, theme="Engineering", label="negative", n=4)

    rows = store.run_metric("sentiment_by_theme", org_id=org, workspace_id=None,
                            period="month", days=365, group_by="subject")
    assert rows == [], "four responses must be suppressed, not rounded"


def test_a_group_at_the_floor_is_shown(org):
    """The floor is >=, not >. Five is the conventional reporting minimum, and
    an off-by-one here silently hides real data."""
    _sentiment(org, theme="Engineering", label="positive", n=5)

    rows = store.run_metric("sentiment_by_theme", org_id=org, workspace_id=None,
                            period="month", days=365, group_by="subject")
    assert sum(r.value for r in rows) == 5


def test_the_floor_applies_per_group_not_to_the_total(org):
    """The whole point: a big theme must not carry a small one over the line."""
    _sentiment(org, theme="Engineering", label="positive", n=6)
    _sentiment(org, theme="Design", label="negative", n=2)

    rows = store.run_metric("sentiment_by_theme", org_id=org, workspace_id=None,
                            period="month", days=365, group_by="subject")
    assert {r.group for r in rows} == {"Engineering"}


def test_metrics_without_a_floor_are_unaffected(org):
    """A page count is not sensitive -- everyone can already retrieve those
    pages -- so a floor there would hide real data for no reason."""
    _fact(org)
    rows = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                            period="month", days=365)
    assert sum(r.value for r in rows) == 1


# --------------------------------------------------------------------------
# There is no `space` dimension, and that is structural
# --------------------------------------------------------------------------


def test_space_is_not_a_dimension():
    """It shipped, rendered one bar labelled with a raw workspace UUID, and
    could never have rendered anything else: isolation scopes every query to
    ONE workspace_id, so grouping by it returns a single bucket. A real
    cross-space breakdown would have to read rows the asker's scope excludes.
    """
    assert "space" not in registry.DIMENSIONS
    assert all("space" not in m.dims for m in registry.METRICS.values())
    assert all(m.series_by != "space" for m in registry.METRICS.values())


def test_no_panel_groups_by_space():
    from app.insights import panels

    assert all(
        p.group_by != "space"
        for group in panels.PANELS.values()
        for p in group
    )
    panels.validate()


def test_asking_for_a_space_breakdown_is_refused_not_drawn(org):
    """A dimension that is gone must raise rather than silently ungroup: a
    chart quietly answering a different question than the one asked is the
    failure this whole package is arranged against."""
    _fact(org, provider="slack", kind="doc_changed")

    with pytest.raises(ValueError):
        store.run_metric(
            "slack_threads", org_id=org, workspace_id=None,
            period="month", days=90, group_by="space",
        )
