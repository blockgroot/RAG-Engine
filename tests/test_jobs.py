"""Phase 12: Postgres-backed ingestion job queue + worker.

DB-backed tests (queue mechanics, isolation) use the real Postgres+pgvector
store — skipped automatically without DATABASE_URL. The worker's ingestion
step is faked (no real Notion/LLM/embedding calls) so `run_once` tests stay
fast and deterministic; the queue mechanics themselves are exercised for real.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import OAuthTokens, save_connection
from app.core.exceptions import ConfigurationError
from app.db.connection import get_connection
from app.jobs import queue

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


@pytest.fixture
def _connected_org(store, org_cleanup):
    """An org with a saved (fake) Notion connection, returning (org_id, connection_id)."""
    org_id = store.create_organization("Jobs Test Org")
    org_cleanup.append(org_id)
    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_fake",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-jobs-test",
        ),
    )
    return org_id, connection_id


@requires_db
def test_enqueue_and_get_job(_connected_org):
    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)

    job = queue.get_job(org_id, job_id)
    assert job is not None
    assert job.status == "queued"
    assert job.org_id == org_id
    assert job.connection_id == connection_id


@requires_db
def test_get_job_scoped_to_org_never_returns_another_orgs_job(_connected_org, store, org_cleanup):
    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)

    other_org = store.create_organization("Jobs Test Org - Other")
    org_cleanup.append(other_org)

    assert queue.get_job(other_org, job_id) is None
    assert queue.get_job(org_id, job_id) is not None


@requires_db
def test_list_jobs_scoped_to_org(_connected_org, store, org_cleanup):
    org_id, connection_id = _connected_org
    queue.enqueue(org_id, connection_id)
    queue.enqueue(org_id, connection_id)

    other_org, other_connection = _make_connected_org(store, org_cleanup)
    queue.enqueue(other_org, other_connection)

    org_jobs = queue.list_jobs(org_id)
    assert len(org_jobs) == 2
    assert all(j.org_id == org_id for j in org_jobs)


def _make_connected_org(store, org_cleanup):
    org_id = store.create_organization("Jobs Test Org - Helper")
    org_cleanup.append(org_id)
    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_fake_2",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-helper",
        ),
    )
    return org_id, connection_id


@requires_db
def test_claim_next_returns_queued_jobs_oldest_first_and_marks_running(_connected_org):
    org_id, connection_id = _connected_org
    first = queue.enqueue(org_id, connection_id)
    second = queue.enqueue(org_id, connection_id)

    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.id == first
    assert claimed.status == "running"
    assert claimed.started_at is not None

    # The second job is still queued and untouched.
    still_queued = queue.get_job(org_id, second)
    assert still_queued.status == "queued"


@requires_db
def test_claim_next_never_claims_the_same_job_twice(_connected_org):
    org_id, connection_id = _connected_org
    queue.enqueue(org_id, connection_id)

    first_claim = queue.claim_next()
    second_claim = queue.claim_next()  # queue now empty for this job
    assert first_claim is not None
    assert second_claim is None or second_claim.id != first_claim.id


@requires_db
def test_mark_succeeded_and_mark_failed(_connected_org):
    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)
    queue.claim_next()

    queue.mark_succeeded(job_id, doc_count=7)
    job = queue.get_job(org_id, job_id)
    assert job.status == "succeeded"
    assert job.doc_count == 7
    assert job.finished_at is not None

    job_id_2 = queue.enqueue(org_id, connection_id)
    queue.claim_next()
    queue.mark_failed(job_id_2, error="boom")
    job2 = queue.get_job(org_id, job_id_2)
    assert job2.status == "failed"
    assert job2.error == "boom"


@requires_db
def test_reap_stuck_flips_old_running_jobs_to_failed(_connected_org):
    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)
    queue.claim_next()

    # Backdate started_at so it looks stuck past the timeout.
    with get_connection() as conn:
        conn.execute(
            "UPDATE ingestion_jobs SET started_at = %s WHERE id = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=45), job_id),
        )

    reaped = queue.reap_stuck(timeout_minutes=30)
    assert reaped >= 1

    job = queue.get_job(org_id, job_id)
    assert job.status == "failed"
    assert job.error == "worker timeout"


@requires_db
def test_reap_stuck_leaves_recent_running_jobs_alone(_connected_org):
    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)
    queue.claim_next()

    queue.reap_stuck(timeout_minutes=30)  # job just started, well within timeout

    job = queue.get_job(org_id, job_id)
    assert job.status == "running"


@requires_db
def test_get_connection_provider_scoped_to_org(_connected_org, store, org_cleanup):
    org_id, connection_id = _connected_org
    assert queue.get_connection_provider(connection_id, org_id) == "notion"

    other_org = store.create_organization("Jobs Test Org - Provider Check")
    org_cleanup.append(other_org)
    with pytest.raises(ConfigurationError):
        queue.get_connection_provider(connection_id, other_org)


# -- worker (ingestion faked, queue mechanics real) ----------------------------


@requires_db
def test_worker_run_once_marks_job_succeeded(_connected_org, monkeypatch):
    from app.jobs import worker

    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)

    class FakeIngestResult:
        documents_ingested = 3

    monkeypatch.setattr(worker, "get_connection_token", lambda org, provider: "ntn_fake")
    monkeypatch.setattr(worker, "build_source_adapter", lambda provider, token: object())
    monkeypatch.setattr(worker, "ingest_source", lambda adapter, org: FakeIngestResult())

    result = worker.run_once()
    assert result is not None
    assert result.id == job_id
    assert result.status == "succeeded"
    assert result.doc_count == 3


@requires_db
def test_worker_run_once_marks_job_failed_on_ingestion_error(_connected_org, monkeypatch):
    from app.jobs import worker

    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)

    def _boom(*args, **kwargs):
        raise RuntimeError("ingestion exploded")

    monkeypatch.setattr(worker, "get_connection_token", lambda org, provider: "ntn_fake")
    monkeypatch.setattr(worker, "build_source_adapter", lambda provider, token: object())
    monkeypatch.setattr(worker, "ingest_source", _boom)

    result = worker.run_once()
    assert result is not None
    assert result.id == job_id
    assert result.status == "failed"
    assert "ingestion exploded" in result.error


@requires_db
def test_worker_run_once_returns_none_when_queue_empty(monkeypatch):
    from app.jobs import worker

    # Drain any leftovers from other tests in this session isn't guaranteed,
    # so just assert the contract on a job-less claim: if nothing is queued,
    # run_once returns None rather than raising.
    while queue.claim_next() is not None:
        pass  # drain anything left running->already claimed doesn't apply; queued only

    assert worker.run_once() is None
