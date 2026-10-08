"""Charts on THIN data: a tool connected last week, one file edited once.

What staging showed: "people per month" as one bar, "files per week" as one
bar beside an empty week. True numbers, drawn as if the chart had failed. A
trend now steps to a finer period until it has a real axis, says it did, and
says plainly when everything so far sits in one period.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.agent.insights_agent import InsightsAgent, MIN_TREND_BUCKETS
from app.db import get_connection
from app.insights.resolve import ChartSpec
from app.vectorstore.base import Viewer
from .conftest import requires_db

pytestmark = requires_db

EVERYONE = Viewer.unrestricted()


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        org = str(conn.execute("INSERT INTO organizations (name) VALUES (%s) RETURNING id",
                               (f"thin-{uuid.uuid4().hex[:8]}",)).fetchone()[0])
        conn.commit()
    org_cleanup.append(org)
    return org


def _commit(org, days_ago, actor="ada"):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO activity_facts (org_id, provider, kind, actor, subject, occurred_at, "
            "external_id) VALUES (%s, 'github', 'commit', %s, 'acme/api', %s, %s)",
            (org, actor, datetime.now(timezone.utc) - timedelta(days=days_ago),
             uuid.uuid4().hex),
        )
        conn.commit()


def _chart(org, period, measure=None):
    return InsightsAgent().answer(
        "q", org, viewer=EVERYONE, user_id="u", role="member",
        spec=ChartSpec(metric="commits_by_author", group_by=None, period=period,
                       chart="line", measure=measure),
    )


def test_a_monthly_trend_over_a_week_of_data_is_drawn_per_day(org):
    _commit(org, 6)
    _commit(org, 2)
    response = _chart(org, "month")
    buckets = {p["bucket"] for p in response.chart["points"]}
    assert response.chart_period == "day"
    assert len(buckets) >= MIN_TREND_BUCKETS
    assert "Shown per day" in response.chart["caveat"]
    assert sum(p["value"] for p in response.chart["points"]) == 2


def test_a_trend_with_enough_periods_keeps_the_asked_period(org):
    for weeks in (1, 3, 5, 7):
        _commit(org, weeks * 7)
    response = _chart(org, "week")
    assert response.chart_period == "week"
    assert "Shown per" not in (response.chart["caveat"] or "")


def test_all_activity_in_one_period_is_said_plainly(org):
    _commit(org, 3)
    _commit(org, 3)
    response = _chart(org, "week")
    assert "All activity so far (2 commits) is in one day." in response.chart["caveat"]
