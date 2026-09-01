"""Background connection syncing: freshness without a human pressing Check.

Real Postgres — the whole feature IS the SQL (which rows are due, and the
read-and-clear that collapses a burst), so faking the DB would test nothing.
No network: ``enqueue_due_syncs`` only writes queue rows; the worker that
would actually fetch is a separate, already-tested path.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth import OAuthTokens, save_connection
from app.config.settings import AutoSyncSettings
from app.db.connection import get_connection
from app.jobs import autosync, queue

from .conftest import requires_db

SETTINGS = AutoSyncSettings(enabled=True, interval_hours=6, batch_size=5)


@pytest.fixture(autouse=True)
def _auth_encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


@pytest.fixture
def connected_org(store, org_cleanup):
    """An org with one Slack connection. Returns ``(org_id, connection_id)``."""
    org_id = store.create_organization(f"AutoSync Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    connection_id = save_connection(
        org_id,
        "slack",
        OAuthTokens(
            access_token="xoxb-fake",
            refresh_token=None,
            expires_at=None,
            external_workspace_id=f"T{uuid.uuid4().hex[:8]}",
        ),
    )
    return org_id, connection_id


def _sync_state(connection_id: str) -> tuple:
    with get_connection() as conn:
        return conn.execute(
            "SELECT sync_requested_at, last_sync_at FROM oauth_connections "
            "WHERE id = %s",
            (connection_id,),
        ).fetchone()


def _clear_jobs(org_id: str) -> None:
    """Drop this org's queue rows so a second tick can enqueue again."""
    with get_connection() as conn:
        conn.execute("DELETE FROM ingestion_jobs WHERE org_id = %s", (org_id,))


# --------------------------------------------------------------------------
# The interval floor
# --------------------------------------------------------------------------


@requires_db
def test_a_never_synced_connection_is_due_immediately(connected_org):
    """``last_sync_at IS NULL`` must qualify. Without the NULL arm a brand-new
    connection would wait a full interval before its first sync, which reads as
    "connecting did nothing"."""
    org_id, connection_id = connected_org

    assert autosync.enqueue_due_syncs(SETTINGS) >= 1

    with get_connection() as conn:
        jobs = conn.execute(
            "SELECT connection_id::text FROM ingestion_jobs WHERE org_id = %s",
            (org_id,),
        ).fetchall()
    assert [connection_id] == [j[0] for j in jobs]


@requires_db
def test_a_freshly_synced_connection_is_not_due_again(connected_org):
    """The interval is what stops every tick from re-queueing the same work."""
    org_id, connection_id = connected_org
    autosync.enqueue_due_syncs(SETTINGS)
    _clear_jobs(org_id)

    # last_sync_at was just stamped, so the second tick must skip this row.
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT count(*) FROM ingestion_jobs WHERE org_id = %s", (org_id,)
        ).fetchone()
    assert rows[0] == 0

    autosync.enqueue_due_syncs(SETTINGS)

    with get_connection() as conn:
        again = conn.execute(
            "SELECT count(*) FROM ingestion_jobs WHERE org_id = %s", (org_id,)
        ).fetchone()
    assert again[0] == 0


@requires_db
def test_an_aged_connection_becomes_due_again(connected_org):
    org_id, connection_id = connected_org
    autosync.enqueue_due_syncs(SETTINGS)
    _clear_jobs(org_id)

    with get_connection() as conn:
        conn.execute(
            "UPDATE oauth_connections SET last_sync_at = now() - interval '7 hours' "
            "WHERE id = %s",
            (connection_id,),
        )

    assert autosync.enqueue_due_syncs(SETTINGS) >= 1


# --------------------------------------------------------------------------
# The webhook flag
# --------------------------------------------------------------------------


@requires_db
def test_a_webhook_makes_a_not_yet_due_connection_due(connected_org):
    """Push is the point: a service saying "something changed" must not wait
    out the remaining interval."""
    org_id, connection_id = connected_org
    autosync.enqueue_due_syncs(SETTINGS)  # stamps last_sync_at = now()
    _clear_jobs(org_id)

    assert autosync.request_sync(org_id, "slack") == 1
    assert autosync.enqueue_due_syncs(SETTINGS) == 1


@requires_db
def test_a_burst_of_webhooks_produces_one_job_and_clears_the_flag(connected_org):
    """A busy channel stamps the flag per message. The read-and-clear is the
    entire debounce — fifty stamps, one job."""
    org_id, connection_id = connected_org
    autosync.enqueue_due_syncs(SETTINGS)
    _clear_jobs(org_id)

    for _ in range(50):
        autosync.request_sync(org_id, "slack")

    requested, _ = _sync_state(connection_id)
    assert requested is not None

    assert autosync.enqueue_due_syncs(SETTINGS) == 1

    requested_after, last_sync = _sync_state(connection_id)
    assert requested_after is None, "the flag must be cleared, or every tick re-queues"
    assert last_sync is not None

    with get_connection() as conn:
        count = conn.execute(
            "SELECT count(*) FROM ingestion_jobs WHERE org_id = %s", (org_id,)
        ).fetchone()
    assert count[0] == 1


@requires_db
def test_request_sync_does_not_touch_another_orgs_connection(store, org_cleanup):
    """Tenant scoping on the write path, not only on reads."""
    other_org = store.create_organization(f"AutoSync Other {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other_org)
    save_connection(
        other_org,
        "slack",
        OAuthTokens(
            access_token="xoxb-other",
            refresh_token=None,
            expires_at=None,
            external_workspace_id=f"T{uuid.uuid4().hex[:8]}",
        ),
    )

    assert autosync.request_sync(str(uuid.uuid4()), "slack") == 0


# --------------------------------------------------------------------------
# Failing closed
# --------------------------------------------------------------------------


@requires_db
def test_a_connection_needing_reauth_is_never_synced(connected_org):
    """A dead token cannot be fixed by retrying it, and retrying every tick is
    how an org gets rate-limited for a problem only a reconnect solves."""
    org_id, connection_id = connected_org
    with get_connection() as conn:
        conn.execute(
            "UPDATE oauth_connections SET needs_reauth = true WHERE id = %s",
            (connection_id,),
        )

    assert autosync.enqueue_due_syncs(SETTINGS) == 0


@requires_db
def test_an_already_active_job_is_a_noop_not_an_error(connected_org):
    """The unique partial index refuses the duplicate; that is the correct
    outcome (the work is already happening), so the tick must not raise."""
    org_id, connection_id = connected_org
    queue.enqueue(org_id, connection_id)

    with get_connection() as conn:
        conn.execute(
            "UPDATE oauth_connections SET sync_requested_at = now() WHERE id = %s",
            (connection_id,),
        )

    assert autosync.enqueue_due_syncs(SETTINGS) == 0


@requires_db
def test_disabled_means_disabled(connected_org):
    off = AutoSyncSettings(enabled=False, interval_hours=6, batch_size=5)
    assert autosync.enqueue_due_syncs(off) == 0


@requires_db
def test_batch_size_bounds_one_tick(store, org_cleanup):
    """A 40-connection org must not turn one tick into 40 simultaneous ingests
    on a 512MB box."""
    org_id = store.create_organization(f"AutoSync Batch {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    for provider in ("slack", "notion", "linear", "google"):
        save_connection(
            org_id,
            provider,
            OAuthTokens(
                access_token=f"tok-{provider}",
                refresh_token=None,
                expires_at=None,
                external_workspace_id=f"ext-{uuid.uuid4().hex[:6]}",
            ),
        )

    capped = AutoSyncSettings(enabled=True, interval_hours=6, batch_size=2)
    assert autosync.enqueue_due_syncs(capped) == 2
