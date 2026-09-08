"""`sync_now` — the first ingest happens on connect, not on the next tick."""

from __future__ import annotations

import pytest

from app.jobs import autosync, queue


class _Calls:
    def __init__(self):
        self.enqueued: list[tuple] = []
        self.stamped: list[str] = []


@pytest.fixture
def calls(monkeypatch):
    c = _Calls()

    def fake_enqueue(org_id, connection_id, workspace_id=None):
        c.enqueued.append((org_id, connection_id, workspace_id))
        return "job-1"

    monkeypatch.setattr(queue, "enqueue", fake_enqueue)
    monkeypatch.setattr(autosync, "_stamp_attempted", lambda cid: c.stamped.append(cid))
    return c


def test_queues_and_stamps(calls):
    assert autosync.sync_now("org", "conn", provider="notion") == "job-1"
    assert calls.enqueued == [("org", "conn", None)]
    # Stamped like the tick does, or this connection re-qualifies as due
    # immediately and earns a second job behind the first.
    assert calls.stamped == ["conn"]


def test_workspace_scope_is_passed(calls):
    autosync.sync_now("org", "conn", provider="slack", workspace_id="ws")
    assert calls.enqueued == [("org", "conn", "ws")]


def test_github_is_never_queued(calls):
    # It has no ingestion path at all; queueing one is the "Unknown source
    # type" failure UNSYNCABLE_PROVIDERS exists to prevent.
    assert autosync.sync_now("org", "conn", provider="github") is None
    assert calls.enqueued == []
    assert calls.stamped == []


def test_active_job_is_a_no_op(monkeypatch, calls):
    def busy(*_a, **_k):
        raise queue.JobAlreadyActiveError("already running")

    monkeypatch.setattr(queue, "enqueue", busy)
    assert autosync.sync_now("org", "conn", provider="linear") is None
    # Not stamped: nothing was attempted here, and the running job stamps itself.
    assert calls.stamped == []


def test_queue_failure_never_breaks_the_caller(monkeypatch, calls):
    # It is called from the OAuth callback and the scope save. A queue problem
    # must not fail a connect that already succeeded — the tick still catches it.
    monkeypatch.setattr(queue, "enqueue", lambda *a, **k: 1 / 0)
    assert autosync.sync_now("org", "conn", provider="notion") is None


def test_scoped_providers_are_the_ones_that_need_a_folder_or_channels():
    # Connect-time queueing skips these; their adapters raise without a scope,
    # so an eager job would only ever fail.
    assert set(autosync.SCOPED_PROVIDERS) == {"google", "slack"}
