"""Prompt-Driven Activity Scheduler, Phase 1: store CRUD + claim queue.

Exercised against real Postgres — the whole point of the claim query is its
concurrency behaviour (``FOR UPDATE SKIP LOCKED``, atomic status flip), which
a mock cannot prove. No LLM, no source API, no email: this phase is storage
and scheduling mechanics only.
"""

from __future__ import annotations

import pytest

from app.auth import OAuthTokens, save_connection
from app.auth.users import invite_member
from app.db.connection import get_connection
from app.jobs import scheduler_queue
from app.schedulers import store as sched_store
from app.schedulers.store import SchedulerError

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


@pytest.fixture
def scheduler_org(store, org_cleanup):
    """An org with a member and a fake org-wide Slack connection.

    Returns ``(org_id, user_id, connection_id)``. The connection is never
    called in this phase — a scheduler only needs to reference one.
    """
    org_id = store.create_organization("Scheduler Queue Test Org")
    org_cleanup.append(org_id)
    user = invite_member(f"sched-{org_id[:8]}@example.com", org_id)
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
    """Backdate a scheduler so the claim query sees it as due."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE schedulers SET next_run_at = now() - interval '1 minute' "
            "WHERE id = %s",
            (scheduler_id,),
        )


@requires_db
def test_create_lists_and_deletes_only_the_owners_scheduler(scheduler_org):
    org_id, user_id, connection_id = scheduler_org
    other = invite_member(f"other-{org_id[:8]}@example.com", org_id)

    sched = sched_store.create_scheduler(
        org_id, user_id, connection_id, "slack", "weekly", "What shipped?"
    )

    assert [s.id for s in sched_store.list_schedulers(org_id, user_id)] == [sched.id]
    # Same org, different member: must not see or be able to delete it.
    assert sched_store.list_schedulers(org_id, other.id) == []
    assert sched_store.delete_scheduler(org_id, other.id, sched.id) is False
    assert sched_store.delete_scheduler(org_id, user_id, sched.id) is True
    assert sched_store.list_schedulers(org_id, user_id) == []


@requires_db
def test_create_rejects_unsupported_provider_and_frequency(scheduler_org):
    org_id, user_id, connection_id = scheduler_org
    with pytest.raises(SchedulerError):
        sched_store.create_scheduler(
            org_id, user_id, connection_id, "notion", "weekly", "x"
        )
    with pytest.raises(SchedulerError):
        sched_store.create_scheduler(
            org_id, user_id, connection_id, "slack", "hourly", "x"
        )
    with pytest.raises(SchedulerError):
        sched_store.create_scheduler(
            org_id, user_id, connection_id, "slack", "weekly", "   "
        )


@requires_db
def test_claim_due_returns_only_due_schedulers(scheduler_org):
    org_id, user_id, connection_id = scheduler_org
    due = sched_store.create_scheduler(
        org_id, user_id, connection_id, "slack", "weekly", "due one"
    )
    not_due = sched_store.create_scheduler(
        org_id, user_id, connection_id, "slack", "weekly", "not due"
    )
    _make_due(due.id)

    claimed_ids = {s.id for s in scheduler_queue.claim_due(limit=10)}
    assert due.id in claimed_ids
    assert not_due.id not in claimed_ids

    # Claimed rows are 'running', so a second poll finds nothing new.
    assert due.id not in {s.id for s in scheduler_queue.claim_due(limit=10)}


@requires_db
def test_claim_increments_attempts_and_success_resets_them(scheduler_org):
    org_id, user_id, connection_id = scheduler_org
    sched = sched_store.create_scheduler(
        org_id, user_id, connection_id, "slack", "weekly", "attempts"
    )
    _make_due(sched.id)

    claimed = [s for s in scheduler_queue.claim_due(limit=10) if s.id == sched.id]
    assert claimed and claimed[0].attempts == 1

    scheduler_queue.mark_run_success(sched.id, "weekly")
    after = sched_store.get_scheduler(org_id, user_id, sched.id)
    assert after.status == "active"
    assert after.attempts == 0
    assert after.last_run_at is not None
    # Next run is a full interval out, so it is no longer due.
    assert after.next_run_at > after.last_run_at
    assert scheduler_queue.claim_due(limit=10) == [] or sched.id not in {
        s.id for s in scheduler_queue.claim_due(limit=10)
    }


@requires_db
def test_repeated_failure_retires_the_scheduler_instead_of_retrying_forever(
    scheduler_org,
):
    """A scheduler that fails every run must stop, not poll a dead service forever."""
    org_id, user_id, connection_id = scheduler_org
    sched = sched_store.create_scheduler(
        org_id, user_id, connection_id, "slack", "weekly", "always fails"
    )

    for _ in range(3):
        _make_due(sched.id)
        assert sched.id in {s.id for s in scheduler_queue.claim_due(limit=10)}
        scheduler_queue.mark_run_failed(sched.id, "boom", max_attempts=3)

    final = sched_store.get_scheduler(org_id, user_id, sched.id)
    assert final.status == "failed"
    assert final.last_error == "boom"
    # Retired: no longer claimable even when backdated.
    _make_due(sched.id)
    assert sched.id not in {s.id for s in scheduler_queue.claim_due(limit=10)}


@requires_db
def test_one_schedulers_failure_leaves_the_others_untouched(scheduler_org):
    """Independent failure is a stated requirement — prove it at the queue level."""
    org_id, user_id, connection_id = scheduler_org
    bad = sched_store.create_scheduler(
        org_id, user_id, connection_id, "slack", "weekly", "fails"
    )
    good = sched_store.create_scheduler(
        org_id, user_id, connection_id, "slack", "weekly", "works"
    )
    _make_due(bad.id)
    _make_due(good.id)
    scheduler_queue.claim_due(limit=10)

    scheduler_queue.mark_run_failed(bad.id, "revoked token", max_attempts=3)
    scheduler_queue.mark_run_success(good.id, "weekly")

    assert sched_store.get_scheduler(org_id, user_id, bad.id).last_error is not None
    good_after = sched_store.get_scheduler(org_id, user_id, good.id)
    assert good_after.last_error is None
    assert good_after.last_run_at is not None


@requires_db
def test_requeue_returns_orphans_but_abandons_a_run_that_keeps_killing_the_worker(
    scheduler_org,
):
    """A crash never reaches mark_run_failed, so the cap must live here too."""
    org_id, user_id, connection_id = scheduler_org
    sched = sched_store.create_scheduler(
        org_id, user_id, connection_id, "slack", "weekly", "crashes the worker"
    )

    # Two simulated crashes: claimed (attempts 1, 2), process died, requeued.
    for _ in range(2):
        _make_due(sched.id)
        scheduler_queue.claim_due(limit=10)
        assert scheduler_queue.requeue_interrupted_running(max_attempts=3) >= 1
        assert sched_store.get_scheduler(org_id, user_id, sched.id).status == "active"

    # Third claim reaches the cap; this requeue retires it instead.
    _make_due(sched.id)
    scheduler_queue.claim_due(limit=10)
    scheduler_queue.requeue_interrupted_running(max_attempts=3)
    retired = sched_store.get_scheduler(org_id, user_id, sched.id)
    assert retired.status == "failed"
    assert "interrupted" in (retired.last_error or "")


@requires_db
def test_update_changes_prompt_and_rebases_the_next_run(scheduler_org):
    org_id, user_id, connection_id = scheduler_org
    sched = sched_store.create_scheduler(
        org_id, user_id, connection_id, "slack", "weekly", "original"
    )

    edited = sched_store.update_scheduler(
        org_id, user_id, sched.id, prompt="revised", frequency="monthly"
    )
    assert edited.prompt == "revised"
    assert edited.frequency == "monthly"
    # Monthly re-based off creation, so further out than the weekly seed was.
    assert edited.next_run_at > sched.next_run_at

    other = invite_member(f"nope-{org_id[:8]}@example.com", org_id)
    assert sched_store.update_scheduler(org_id, other.id, sched.id, prompt="x") is None
