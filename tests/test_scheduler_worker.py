"""Prompt-Driven Activity Scheduler, Phase 4: the due-scheduler worker tick.

Real Postgres (the claim query and status transitions are the subject), but
``run_scheduler_once`` is stubbed — this phase is about *which* schedulers
run and what the queue records afterwards, not about fetching or generating.

The end-to-end test at the bottom uses the real configured remote LLM, with
GitHub/Slack faked, and is skipped when no LLM is configured.
"""

from __future__ import annotations

import pytest

from app.auth import OAuthTokens, save_connection
from app.auth.users import invite_member
from app.config.settings import SchedulerSettings
from app.db.connection import get_connection
from app.jobs import worker as jobs_worker
from app.schedulers.activity import ActivityDigest, ActivityItem
from app.schedulers import store as sched_store
from app.schedulers import worker as sched_worker

from .conftest import requires_db, requires_llm

SETTINGS = SchedulerSettings(enabled=True, poll_seconds=1, max_attempts=3, batch_size=10)


@pytest.fixture(autouse=True)
def _auth_encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


@pytest.fixture
def scheduler_org(store, org_cleanup):
    org_id = store.create_organization("Scheduler Worker Test Org")
    org_cleanup.append(org_id)
    user = invite_member(f"worker-{org_id[:8]}@example.com", org_id)
    connection_id = save_connection(
        org_id,
        "slack",
        OAuthTokens(
            access_token="xoxb-fake",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="T-fake",
        ),
    )
    return org_id, user.id, connection_id


def _make_due(scheduler_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE schedulers SET next_run_at = now() - interval '1 minute' "
            "WHERE id = %s",
            (scheduler_id,),
        )


def _create(org, prompt, provider="slack", frequency="weekly"):
    org_id, user_id, connection_id = org
    return sched_store.create_scheduler(
        org_id, user_id, connection_id, provider, frequency, prompt
    )


@requires_db
def test_a_due_scheduler_runs_and_its_next_run_advances(monkeypatch, scheduler_org):
    org_id, user_id, _ = scheduler_org
    sched = _create(scheduler_org, "weekly summary")
    _make_due(sched.id)
    ran: list[str] = []
    monkeypatch.setattr(
        sched_worker, "run_scheduler_once", lambda s: ran.append(s.id), raising=False
    )
    monkeypatch.setattr(
        "app.schedulers.runner.run_scheduler_once", lambda s: ran.append(s.id)
    )

    assert sched_worker.run_due_schedulers_once(SETTINGS) >= 1

    assert sched.id in ran
    after = sched_store.get_scheduler(org_id, user_id, sched.id)
    assert after.status == "active"
    assert after.last_run_at is not None
    assert after.next_run_at > after.last_run_at
    assert after.attempts == 0


@requires_db
def test_a_not_yet_due_scheduler_is_left_alone(monkeypatch, scheduler_org):
    org_id, user_id, _ = scheduler_org
    sched = _create(scheduler_org, "not due yet")
    monkeypatch.setattr(
        "app.schedulers.runner.run_scheduler_once",
        lambda s: pytest.fail("should not have run a scheduler that isn't due"),
    )

    sched_worker.run_due_schedulers_once(SETTINGS)

    after = sched_store.get_scheduler(org_id, user_id, sched.id)
    assert after.last_run_at is None
    assert after.status == "active"


@requires_db
def test_a_failing_scheduler_records_the_error_and_stays_retryable(
    monkeypatch, scheduler_org
):
    org_id, user_id, _ = scheduler_org
    sched = _create(scheduler_org, "will fail")
    _make_due(sched.id)
    monkeypatch.setattr(
        "app.schedulers.runner.run_scheduler_once",
        lambda s: (_ for _ in ()).throw(RuntimeError("slack is down")),
    )

    sched_worker.run_due_schedulers_once(SETTINGS)

    after = sched_store.get_scheduler(org_id, user_id, sched.id)
    assert after.status == "active"  # under the cap, so retryable
    assert "slack is down" in (after.last_error or "")
    assert after.last_run_at is None  # window not advanced — nothing was delivered


@requires_db
def test_one_failure_does_not_stop_the_others_in_the_same_batch(
    monkeypatch, scheduler_org
):
    """A stated requirement: schedulers are independent."""
    org_id, user_id, _ = scheduler_org
    bad = _create(scheduler_org, "explodes")
    good_a = _create(scheduler_org, "fine A")
    good_b = _create(scheduler_org, "fine B")
    for s in (bad, good_a, good_b):
        _make_due(s.id)

    def _run(scheduler):
        if scheduler.id == bad.id:
            raise RuntimeError("boom")

    monkeypatch.setattr("app.schedulers.runner.run_scheduler_once", _run)

    sched_worker.run_due_schedulers_once(SETTINGS)

    assert sched_store.get_scheduler(org_id, user_id, bad.id).last_error is not None
    for good in (good_a, good_b):
        after = sched_store.get_scheduler(org_id, user_id, good.id)
        assert after.last_run_at is not None, f"{good.prompt} should still have run"
        assert after.last_error is None


@requires_db
def test_batch_size_bounds_how_many_run_per_tick(monkeypatch, scheduler_org):
    """A burst of due schedulers must not monopolise the shared worker thread."""
    for i in range(5):
        _make_due(_create(scheduler_org, f"burst {i}").id)
    ran: list[str] = []
    monkeypatch.setattr(
        "app.schedulers.runner.run_scheduler_once", lambda s: ran.append(s.id)
    )

    processed = sched_worker.run_due_schedulers_once(
        SchedulerSettings(enabled=True, poll_seconds=1, max_attempts=3, batch_size=2)
    )

    assert processed == 2
    assert len(ran) == 2


@requires_db
def test_the_worker_tick_never_raises_into_the_shared_loop(monkeypatch):
    """The ingestion tick shares this loop; a scheduler error must not abort it."""
    monkeypatch.setattr(
        "app.schedulers.worker.run_due_schedulers_once",
        lambda settings=None: (_ for _ in ()).throw(RuntimeError("db gone")),
    )

    assert jobs_worker.run_scheduler_tick(SETTINGS) == 0  # swallowed, not raised


@requires_db
def test_a_disabled_scheduler_setting_stops_the_tick_firing(monkeypatch, scheduler_org):
    """Kill-switch: SCHEDULER_ENABLED=false must stop runs, not just new rows."""
    sched = _create(scheduler_org, "should not run")
    _make_due(sched.id)
    monkeypatch.setattr(
        "app.schedulers.runner.run_scheduler_once",
        lambda s: pytest.fail("disabled scheduler should not have run"),
    )

    off = SchedulerSettings(
        enabled=False, poll_seconds=1, max_attempts=3, batch_size=10
    )
    # Guarded inside run_due_schedulers_once, not only in the loops' timers,
    # so the switch also holds for a direct/manual call.
    assert sched_worker.run_due_schedulers_once(off) == 0

    # Not claimed either — still due and untouched once re-enabled.
    org_id, user_id, _ = scheduler_org
    assert sched_store.get_scheduler(org_id, user_id, sched.id).status == "active"


# --------------------------------------------------------------------------
# End-to-end: real LLM, faked source HTTP
# --------------------------------------------------------------------------


@requires_db
@requires_llm
def test_end_to_end_produces_a_real_report_with_the_configured_llm(
    monkeypatch, scheduler_org, capsys
):
    """Due scheduler -> faked activity -> REAL remote LLM -> console email.

    Proves the whole Phase 1-4 chain against the actually-configured model
    (no local embedding/reranker model is touched anywhere in this path).
    """
    org_id, user_id, _ = scheduler_org
    sched = _create(scheduler_org, "Summarise what the team worked on.")
    _make_due(sched.id)

    items = (
        ActivityItem(
            "[2026-08-19 10:02] #eng alice: merged the billing retry fix",
            "https://slack.com/archives/C1/p1000",
        ),
        ActivityItem(
            "[2026-08-19 14:40] #eng bob: staging deploy is green",
            "https://slack.com/archives/C1/p2000",
        ),
    )
    monkeypatch.setattr(
        "app.schedulers.runner.fetch_activity",
        lambda provider, org, since, workspace_id=None: ActivityDigest(
            items=items,
            notes=("Channels checked: #eng.",),
            text="\n".join(i.summary for i in items),
        ),
    )
    monkeypatch.setenv("EMAIL_SENDER", "console")

    processed = sched_worker.run_due_schedulers_once(SETTINGS)

    assert processed == 1
    after = sched_store.get_scheduler(org_id, user_id, sched.id)
    assert after.status == "active", f"run failed: {after.last_error}"
    assert after.last_run_at is not None
    assert after.next_run_at > after.last_run_at

    printed = capsys.readouterr().out
    assert "[email:console]" in printed
    # The model was given billing/deploy activity; a real report mentions it.
    assert "billing" in printed.lower() or "deploy" in printed.lower()
    # Source links come from the template, not the model — so they are always
    # present regardless of what the LLM chose to write.
    assert "https://slack.com/archives/C1/p1000" in printed
    assert "Channels checked: #eng." in printed
