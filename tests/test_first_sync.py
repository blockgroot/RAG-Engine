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


# -- reconnect is not the same event as connect -----------------------------


def test_a_reconnect_with_a_saved_folder_syncs_immediately(monkeypatch):
    """The case people actually hit: a token expires, the connector stops
    syncing, someone reconnects to fix exactly that -- and nothing happened for
    up to an interval, on the one screen where they were watching for it.

    `save_connection` upserts tokens and leaves `source_config` alone, so the
    folder SURVIVES a reconnect. The blanket `SCOPED_PROVIDERS` skip treated
    that like a first connect, which is what made Drive look broken while
    Linear (unscoped) reindexed instantly."""
    from app.jobs import autosync

    monkeypatch.setattr(
        autosync, "get_connection_config", lambda *a, **k: {"folder_id": "F1"},
        raising=False,
    )
    monkeypatch.setattr(
        "app.auth.credentials.get_connection_config", lambda *a, **k: {"folder_id": "F1"}
    )
    queued: list[str] = []
    monkeypatch.setattr(autosync, "sync_now", lambda o, c, **k: queued.append(c) or "job-1")

    assert autosync.sync_after_connect("org", "conn", provider="google") == "job-1"
    assert queued == ["conn"]


def test_a_FIRST_google_connect_still_waits_for_the_folder(monkeypatch):
    """The original reason survives: with no folder saved, GoogleDriveAdapter
    raises, so queueing here would only manufacture a failed job. The
    folder-save route queues it instead."""
    from app.jobs import autosync

    monkeypatch.setattr("app.auth.credentials.get_connection_config", lambda *a, **k: None)
    monkeypatch.setattr(
        autosync, "sync_now",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("queued without a scope")),
    )
    assert autosync.sync_after_connect("org", "conn", provider="google") is None


def test_an_unscoped_provider_always_syncs_on_connect(monkeypatch):
    """Linear/Notion need no scope, so they were never affected -- which is
    exactly the asymmetry that surfaced the bug."""
    from app.jobs import autosync

    queued: list[str] = []
    monkeypatch.setattr(autosync, "sync_now", lambda o, c, **k: queued.append(c) or "job-2")
    assert autosync.sync_after_connect("org", "conn", provider="linear") == "job-2"
    assert queued == ["conn"]


def test_an_unreadable_scope_config_does_not_queue(monkeypatch):
    """Fail closed toward the tick, never toward a job we know will fail."""
    from app.jobs import autosync

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.auth.credentials.get_connection_config", _boom)
    monkeypatch.setattr(
        autosync, "sync_now",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("queued on an unknown scope")),
    )
    assert autosync.sync_after_connect("org", "conn", provider="google") is None


def test_the_scope_key_map_has_exactly_one_definition():
    """`api/notifications` reports "connected but indexing nothing" from the
    same mapping. Two copies drift, and the drift is silent."""
    from app.api import notifications
    from app.jobs.autosync import SCOPE_KEYS, SCOPED_PROVIDERS

    assert notifications._SCOPE_KEYS is SCOPE_KEYS
    assert set(SCOPED_PROVIDERS) == set(SCOPE_KEYS)


@pytest.mark.parametrize(
    "provider, scope_saved, should_sync",
    [
        # Scoped: the skip is correct ONLY while there is nothing to read.
        ("google", False, False),
        ("google", True, True),
        ("slack", False, False),
        ("slack", True, True),
        # Unscoped: a token is the whole configuration, so connecting is always
        # the request to index. These were never affected -- which is exactly
        # the asymmetry that made the Drive bug visible ("Linear reindexed
        # instantly, Drive sat silent").
        ("notion", False, True),
        ("linear", False, True),
    ],
)
def test_connect_sync_matrix(provider, scope_saved, should_sync, monkeypatch):
    """One table for every connector, so this cannot regress per-provider.

    Notion and Linear take no scope at all (`NotionSource` needs only a token),
    so they must never be gated on one; Drive and Slack must be gated on a
    FIRST connect and must NOT be on a reconnect."""
    from app.jobs import autosync

    config = {"folder_id": "F1", "channel_ids": ["C1"]} if scope_saved else None
    monkeypatch.setattr("app.auth.credentials.get_connection_config", lambda *a, **k: config)
    queued: list[str] = []
    monkeypatch.setattr(autosync, "sync_now", lambda o, c, **k: queued.append(c) or "job")

    autosync.sync_after_connect("org", "conn", provider=provider)
    assert bool(queued) is should_sync
