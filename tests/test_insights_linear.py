"""Linear charts: throughput, states, load, cycle time.

The acceptance criterion for this phase is "task completion in team aggregated
by team" -- the request that started this feature -- so it is asserted directly
rather than implied by the pieces.

Linear differs from GitHub in a way that decides where its facts are written:
it ALSO ingests. If its facts rode the sync tick, the ingest path's
``_stamp_attempted`` would already have made the connection not-due, so the
facts would never run. They ride the ingest job instead, where the adapter that
can answer "what moved" already exists.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import get_connection
from app.insights import linear_facts, store
from .conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"lin-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _issue(identifier, *, state="Done", state_type="completed", assignee="priya",
           team="Engineering", created_days=10, completed_days=6):
    now = datetime.now(timezone.utc)
    return {
        "identifier": identifier,
        "title": f"Issue {identifier}",
        "url": f"https://linear.app/acme/issue/{identifier}",
        "state": state,
        "state_type": state_type,
        "assignee": assignee,
        "team": team,
        "created_at": now - timedelta(days=created_days),
        "completed_at": None if completed_days is None else now - timedelta(days=completed_days),
        "at": now - timedelta(days=1),
    }


class FakeAdapter:
    def __init__(self, issues):
        self._issues = issues
        self.since_asked: datetime | None = None

    def fetch_recent_issues(self, since, *, max_issues=300):
        self.since_asked = since
        return self._issues


def _rows(key, org_id, **kw):
    return store.run_metric(key, org_id=org_id, workspace_id=None,
                            period="month", days=365, **kw)


def _total(key, org_id, **kw):
    return sum(r.value for r in _rows(key, org_id, **kw))


# --------------------------------------------------------------------------
# The request that started the feature
# --------------------------------------------------------------------------


def test_task_completion_aggregated_by_team(org):
    """The literal ask: "create a visual representation of task completion in
    team aggregated by team"."""
    adapter = FakeAdapter([
        _issue("ENG-1", team="Engineering"),
        _issue("ENG-2", team="Engineering"),
        _issue("DES-1", team="Design"),
    ])
    linear_facts.record_linear_facts(org, workspace_id=None, adapter=adapter)

    by_team = {}
    for row in _rows("issues_completed", org, group_by="subject"):
        by_team[row.group] = by_team.get(row.group, 0) + row.value
    assert by_team == {"Engineering": 2.0, "Design": 1.0}


def test_completion_can_also_be_split_by_assignee(org):
    adapter = FakeAdapter([
        _issue("ENG-1", assignee="priya"),
        _issue("ENG-2", assignee="priya"),
        _issue("ENG-3", assignee="sam"),
    ])
    linear_facts.record_linear_facts(org, workspace_id=None, adapter=adapter)

    by_person = {}
    for row in _rows("issues_completed", org, group_by="actor"):
        by_person[row.group] = by_person.get(row.group, 0) + row.value
    assert by_person == {"priya": 2.0, "sam": 1.0}


# --------------------------------------------------------------------------
# What counts as completed
# --------------------------------------------------------------------------


def test_only_a_completed_issue_counts_as_completed(org):
    """`state_type` is Linear's own lifecycle category. Counting off the state
    NAME would break the moment a team renames "Done" to "Shipped"."""
    adapter = FakeAdapter([
        _issue("ENG-1", state="Done", state_type="completed"),
        _issue("ENG-2", state="In Progress", state_type="started", completed_days=None),
        _issue("ENG-3", state="Backlog", state_type="backlog", completed_days=None),
    ])
    linear_facts.record_linear_facts(org, workspace_id=None, adapter=adapter)

    assert _total("issues_completed", org) == 1


def test_a_cancelled_issue_is_never_a_completion(org):
    """Linear's `canceled` is a terminal state but not a success, and counting
    it would make a team look productive for abandoning work."""
    adapter = FakeAdapter([
        _issue("ENG-1", state="Cancelled", state_type="canceled", completed_days=2),
    ])
    linear_facts.record_linear_facts(org, workspace_id=None, adapter=adapter)

    assert _total("issues_completed", org) == 0


def test_every_issue_that_moved_is_recorded_as_a_state_fact(org):
    """The funnel needs all of them, not only the finished ones."""
    adapter = FakeAdapter([
        _issue("ENG-1", state="Done", state_type="completed"),
        _issue("ENG-2", state="In Progress", state_type="started", completed_days=None),
        _issue("ENG-3", state="Backlog", state_type="backlog", completed_days=None),
    ])
    linear_facts.record_linear_facts(org, workspace_id=None, adapter=adapter)

    rows = _rows("issue_states", org, group_by="state")
    assert {r.group for r in rows} == {"Done", "In Progress", "Backlog"}


# --------------------------------------------------------------------------
# Cycle time
# --------------------------------------------------------------------------


def test_cycle_time_is_created_to_completed_in_seconds(org):
    adapter = FakeAdapter([_issue("ENG-1", created_days=10, completed_days=6)])
    linear_facts.record_linear_facts(org, workspace_id=None, adapter=adapter)

    with get_connection() as conn:
        value = conn.execute(
            "SELECT value FROM activity_facts "
            " WHERE org_id = %s AND kind = 'issue_completed'",
            (org,),
        ).fetchone()[0]
    assert abs(float(value) - 4 * 86400) < 120, "four days, in seconds"


def test_an_issue_completed_without_a_created_date_has_no_cycle_time(org):
    """A missing date must not become a zero -- a zero drags a median toward
    "instant", which is a claim about speed nobody measured."""
    issue = _issue("ENG-1")
    issue["created_at"] = None
    linear_facts.record_linear_facts(org, workspace_id=None,
                                     adapter=FakeAdapter([issue]))

    with get_connection() as conn:
        value = conn.execute(
            "SELECT value FROM activity_facts "
            " WHERE org_id = %s AND kind = 'issue_completed'",
            (org,),
        ).fetchone()[0]
    assert value is None


def test_a_completed_issue_with_no_completion_date_falls_back_to_updated(org):
    """Linear leaves `completedAt` empty on some older issues. The completion
    is real, so it counts -- dated by when it last moved, which is the closest
    honest answer."""
    issue = _issue("ENG-1", completed_days=None)
    linear_facts.record_linear_facts(org, workspace_id=None,
                                     adapter=FakeAdapter([issue]))
    assert _total("issues_completed", org) == 1


# --------------------------------------------------------------------------
# Bounds, idempotence, failure
# --------------------------------------------------------------------------


def test_re_recording_the_same_issues_does_not_double_the_count(org):
    adapter = FakeAdapter([_issue("ENG-1")])
    linear_facts.record_linear_facts(org, workspace_id=None, adapter=adapter)
    linear_facts.record_linear_facts(org, workspace_id=None, adapter=adapter)
    assert _total("issues_completed", org) == 1


def test_an_issue_that_moves_to_done_later_updates_its_own_fact(org):
    """The same issue is re-read every sync, so an in-progress issue that
    finishes must become a completion rather than a second row."""
    open_issue = _issue("ENG-1", state="In Progress", state_type="started",
                        completed_days=None)
    linear_facts.record_linear_facts(org, workspace_id=None,
                                     adapter=FakeAdapter([open_issue]))
    assert _total("issues_completed", org) == 0

    linear_facts.record_linear_facts(org, workspace_id=None,
                                     adapter=FakeAdapter([_issue("ENG-1")]))
    assert _total("issues_completed", org) == 1
    assert _total("issue_states", org) == 1, "still one issue, not two"


def test_an_adapter_failure_never_raises_out_of_the_recorder(org):
    """This runs inside a job that has ALREADY succeeded. Raising here would
    fail finished work and turn it into a retry loop."""
    class Broken:
        def fetch_recent_issues(self, since, *, max_issues=300):
            raise RuntimeError("Linear 401")

    assert linear_facts.record_linear_facts(org, workspace_id=None,
                                            adapter=Broken()) == 0


def test_the_window_is_bounded_rather_than_all_of_history(org):
    """A first sync against a years-old workspace must not pull every issue
    ever filed into one request."""
    adapter = FakeAdapter([])
    linear_facts.record_linear_facts(org, workspace_id=None, adapter=adapter)

    assert adapter.since_asked is not None
    age = datetime.now(timezone.utc) - adapter.since_asked
    assert 1 < age.days <= 400


def test_the_worker_hook_records_issue_facts_when_it_has_the_adapter(org):
    """The reason the hook takes an adapter at all: only the ingest job has a
    built one, and Linear's issue feed is the only place state/assignee/team
    exist as columns rather than prose."""
    from app.jobs.worker import _record_insight_facts

    _record_insight_facts(org, "linear", None, FakeAdapter([_issue("ENG-1")]))
    assert _total("issues_completed", org) == 1


def test_the_worker_hook_still_works_with_no_adapter(org):
    """Every other caller and every other provider passes none."""
    from app.jobs.worker import _record_insight_facts

    _record_insight_facts(org, "notion", None)  # must not raise


def test_a_broken_linear_feed_does_not_stop_the_document_facts(org):
    """Two independent try/excepts, not one: an issue-feed 401 must not cost
    the `doc_changed` facts that had already been derived from the index."""
    from app.jobs.worker import _record_insight_facts

    class Broken:
        def fetch_recent_issues(self, since, *, max_issues=300):
            raise RuntimeError("Linear 401")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents (org_id, title, source_provider,
                source_external_id, source_last_modified)
            VALUES (%s, 'ENG-1', 'linear', %s, now())
            """,
            (org, uuid.uuid4().hex),
        )
        conn.commit()

    _record_insight_facts(org, "linear", None, Broken())  # must not raise

    docs = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                            period="month", days=365)
    # `docs_changed` is Notion's metric; Linear's document facts share the
    # `doc_changed` kind but are separated by provider -- so this asserts the
    # Linear row landed by counting it where it actually lives.
    with get_connection() as conn:
        count = conn.execute(
            "SELECT count(*) FROM activity_facts "
            " WHERE org_id = %s AND provider = 'linear' AND kind = 'doc_changed'",
            (org,),
        ).fetchone()[0]
    assert count == 1
    assert docs == [], "Notion's metric must not see Linear's rows"
