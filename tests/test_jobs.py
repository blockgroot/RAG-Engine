"""Phase 12: Postgres-backed ingestion job queue + worker.

DB-backed tests (queue mechanics, isolation) use the real Postgres+pgvector
store — skipped automatically without DATABASE_URL. The worker's ingestion
step is faked (no real Notion/LLM/embedding calls) so `run_once` tests stay
fast and deterministic; the queue mechanics themselves are exercised for real.
"""

from __future__ import annotations

import uuid
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
def test_enqueue_rejects_second_active_job(_connected_org):
    org_id, connection_id = _connected_org
    queue.enqueue(org_id, connection_id)
    try:
        queue.enqueue(org_id, connection_id)
        assert False, "expected JobAlreadyActiveError"
    except queue.JobAlreadyActiveError:
        pass


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
    first = queue.enqueue(org_id, connection_id)
    queue.mark_succeeded(first, 0)
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
def test_claim_next_returns_queued_jobs_oldest_first_and_marks_running(
    _connected_org, store, org_cleanup
):
    org_id, connection_id = _connected_org
    other_org, other_connection = _make_connected_org(store, org_cleanup)
    # Two connections: one active job per connection is enforced.
    first = queue.enqueue(org_id, connection_id)
    second = queue.enqueue(other_org, other_connection)

    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.id == first
    assert claimed.status == "running"
    assert claimed.started_at is not None

    # The second job is still queued and untouched.
    still_queued = queue.get_job(other_org, second)
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
def test_requeue_interrupted_running_returns_orphans_to_queued(_connected_org):
    """Worker crash/reload must resume, not leave Updating forever."""
    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)
    claimed = queue.claim_next()
    assert claimed is not None and claimed.status == "running"

    n = queue.requeue_interrupted_running()
    assert n >= 1
    job = queue.get_job(org_id, job_id)
    assert job.status == "queued"
    assert job.started_at is None

    # Can be claimed again (incremental ingest will skip already-stored pages).
    again = queue.claim_next()
    assert again is not None
    assert again.id == job_id


@requires_db
def test_claim_next_counts_attempts(_connected_org):
    """``attempts`` must be incremented AT CLAIM TIME.

    A job that OOM-kills its own process never reaches a later write, so
    counting on completion would leave exactly the jobs we need to bound
    sitting at zero forever.
    """
    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)

    first = queue.claim_next()
    assert first.attempts == 1

    queue.requeue_interrupted_running()
    second = queue.claim_next()
    assert second.id == job_id
    assert second.attempts == 2, "each claim must count"


@requires_db
def test_requeue_abandons_a_job_that_keeps_killing_the_worker(_connected_org):
    """THE crash-loop breaker.

    Unbounded requeuing is how one bad job takes a deployment down forever: the
    job kills the process, boot requeues it, it is claimed and kills the process
    again -- with no traffic and nobody watching. Two live production incidents
    on this project were this loop rather than the underlying bug. Past the
    attempt cap the job must fail loudly and STAY failed, so the API survives.
    """
    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)

    # Simulate three successive boots where the job kills the process mid-run.
    for _ in range(3):
        queue.requeue_interrupted_running(max_attempts=3)
        claimed = queue.claim_next()
        assert claimed is not None, "should still be claimable below the cap"

    # Fourth boot: the cap is reached, so it must be abandoned, not requeued.
    requeued = queue.requeue_interrupted_running(max_attempts=3)

    job = queue.get_job(org_id, job_id)
    assert job.status == "failed", (
        f"a job that has failed {job.attempts} times must not be requeued again"
    )
    assert requeued == 0
    assert "attempts" in (job.error or "").lower()

    # And it must not come back on the next poll.
    assert queue.claim_next() is None


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
    assert job.error.startswith("worker timeout")


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

    monkeypatch.setattr(worker, "get_live_connection_token", lambda org, provider, **kw: "ntn_fake")
    monkeypatch.setattr(
        worker, "build_source_adapter", lambda provider, token=None, config=None, **kw: object()
    )
    monkeypatch.setattr(
        worker, "ingest_source", lambda adapter, org, provider, **kw: FakeIngestResult()
    )

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

    monkeypatch.setattr(worker, "get_live_connection_token", lambda org, provider, **kw: "ntn_fake")
    monkeypatch.setattr(
        worker, "build_source_adapter", lambda provider, token=None, config=None, **kw: object()
    )
    monkeypatch.setattr(worker, "ingest_source", _boom)

    result = worker.run_once()
    assert result is not None
    assert result.id == job_id
    assert result.status == "failed"
    assert "ingestion exploded" in result.error


@requires_db
def test_worker_google_job_passes_folder_config(store, org_cleanup, monkeypatch):
    """A Google job must build the adapter with the connection's folder config."""
    from app.auth import set_connection_config
    from app.jobs import worker

    org_id = store.create_organization("Jobs Google Config Org")
    org_cleanup.append(org_id)
    connection_id = save_connection(
        org_id,
        "google",
        OAuthTokens(
            access_token="goog_access",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="drive-user@example.com",
        ),
    )
    set_connection_config(
        org_id, "google", {"folder_id": "1GoogleFolderId", "folder_name": "Policies"}
    )
    job_id = queue.enqueue(org_id, connection_id)

    captured: dict = {}

    def fake_build(provider, *, token=None, config=None, **kwargs):
        captured["provider"] = provider
        captured["token"] = token
        captured["config"] = config
        return object()

    class FakeIngestResult:
        documents_ingested = 2

    monkeypatch.setattr(worker, "get_live_connection_token", lambda org, provider, **kw: "goog_live")
    monkeypatch.setattr(worker, "build_source_adapter", fake_build)
    monkeypatch.setattr(
        worker, "ingest_source", lambda adapter, org, provider, **kw: FakeIngestResult()
    )

    result = worker.run_once()
    assert result is not None
    assert result.id == job_id
    assert result.status == "succeeded"
    assert captured["provider"] == "google"
    assert captured["token"] == "goog_live"
    assert captured["config"]["folder_id"] == "1GoogleFolderId"


@requires_db
def test_worker_run_once_returns_none_when_queue_empty(monkeypatch):
    from app.jobs import worker

    # Drain any leftovers from other tests in this session isn't guaranteed,
    # so just assert the contract on a job-less claim: if nothing is queued,
    # run_once returns None rather than raising.
    while queue.claim_next() is not None:
        pass  # drain anything left running->already claimed doesn't apply; queued only

    assert worker.run_once() is None


# --- Workspace-within-a-Workspace: workspace-scoped ingestion jobs (Task 8) ---


@requires_db
def test_enqueue_stamps_workspace_id_on_job(store, org_cleanup):
    from app.auth.users import create_admin
    from app.workspaces import create_workspace

    org_id = store.create_organization("Jobs Workspace Org")
    org_cleanup.append(org_id)
    owner = create_admin("owner-jobs@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)
    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_workspace_fake",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-jobs-workspace-test",
        ),
        workspace_id=workspace_id,
    )

    job_id = queue.enqueue(org_id, connection_id, workspace_id=workspace_id)

    job = queue.get_job(org_id, job_id)
    assert job.workspace_id == workspace_id


@requires_db
def test_list_jobs_scoped_to_workspace(store, org_cleanup):
    from app.auth.users import create_admin
    from app.workspaces import create_workspace

    org_id = store.create_organization("Jobs Workspace List Org")
    org_cleanup.append(org_id)
    owner = create_admin("owner-jobs-list@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)

    org_wide_connection = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_orgwide",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-orgwide",
        ),
    )
    workspace_connection = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_workspace",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-personal",
        ),
        workspace_id=workspace_id,
    )
    org_wide_job = queue.enqueue(org_id, org_wide_connection)
    workspace_job = queue.enqueue(org_id, workspace_connection, workspace_id=workspace_id)

    org_wide_jobs = queue.list_jobs(org_id)
    workspace_jobs = queue.list_jobs(org_id, workspace_id=workspace_id)

    assert {j.id for j in org_wide_jobs} == {org_wide_job}
    assert {j.id for j in workspace_jobs} == {workspace_job}


@requires_db
def test_worker_run_once_scopes_ingestion_to_job_workspace(store, org_cleanup, monkeypatch):
    from app.auth.users import create_admin
    from app.jobs import worker
    from app.workspaces import create_workspace

    org_id = store.create_organization("Jobs Worker Workspace Org")
    org_cleanup.append(org_id)
    owner = create_admin("owner-worker@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)
    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_worker_fake",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-worker-test",
        ),
        workspace_id=workspace_id,
    )
    job_id = queue.enqueue(org_id, connection_id, workspace_id=workspace_id)

    captured: dict = {}

    class FakeIngestResult:
        documents_ingested = 1

    monkeypatch.setattr(
        worker, "get_live_connection_token", lambda org, provider, **kw: "ntn_worker_fake"
    )
    monkeypatch.setattr(
        worker, "build_source_adapter", lambda provider, token=None, config=None, **kw: object()
    )

    def _fake_ingest_source(adapter, org, provider, **kw):
        captured["workspace_id"] = kw.get("workspace_id")
        return FakeIngestResult()

    monkeypatch.setattr(worker, "ingest_source", _fake_ingest_source)

    result = worker.run_once()
    assert result is not None
    assert result.id == job_id
    assert result.status == "succeeded"
    assert captured["workspace_id"] == workspace_id


@requires_db
def test_reap_measures_silence_not_age(store, org_cleanup):
    """A healthy long ingest must not be failed just for taking a long time.

    `reap_stuck` used to key off `started_at`, so a slow-but-progressing job (a
    large folder, or contextualization against a 15-rpm endpoint) got marked
    `failed` while it carried on working and the admin was told it had failed.
    The liveness evidence already existed — `update_progress` writes phase and
    counters per document — it just had no timestamp the reaper could read. Same
    wrong-gauge mistake as measuring memory with `ru_maxrss`.
    """
    org_id = store.create_organization(f"Reap Gauge {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    def running_job(provider: str, *, progress_minutes_ago: int | None):
        # One connection each: a partial unique index allows only one active job
        # per connection.
        with get_connection() as conn:
            connection_id = conn.execute(
                "INSERT INTO oauth_connections (org_id, provider, "
                "external_workspace_id, access_token_encrypted) "
                "VALUES (%s::uuid, %s, %s, 'x') RETURNING id::text",
                (org_id, provider, f"ws-{provider}"),
            ).fetchone()[0]
            job_id = conn.execute(
                "INSERT INTO ingestion_jobs (org_id, connection_id, status, started_at) "
                "VALUES (%s::uuid, %s::uuid, 'running', now() - interval '2 hours') "
                "RETURNING id::text",
                (org_id, connection_id),
            ).fetchone()[0]
            if progress_minutes_ago is not None:
                conn.execute(
                    "UPDATE ingestion_jobs SET progress_at = "
                    "now() - (%s || ' minutes')::interval WHERE id = %s::uuid",
                    (progress_minutes_ago, job_id),
                )
        return job_id

    healthy = running_job("notion", progress_minutes_ago=1)
    silent = running_job("google", progress_minutes_ago=90)
    never_reported = running_job("github", progress_minutes_ago=None)

    queue.reap_stuck(timeout_minutes=30)

    with get_connection() as conn:
        statuses = dict(
            conn.execute(
                "SELECT id::text, status FROM ingestion_jobs WHERE org_id = %s::uuid",
                (org_id,),
            ).fetchall()
        )

    assert statuses[healthy] == "running", "a job reporting progress is alive"
    assert statuses[silent] == "failed"
    # coalesce(progress_at, started_at) keeps a job that died before its first
    # report reapable exactly as before.
    assert statuses[never_reported] == "failed"


@requires_db
def test_update_progress_stamps_the_heartbeat(store, org_cleanup):
    org_id = store.create_organization(f"Heartbeat {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    with get_connection() as conn:
        connection_id = conn.execute(
            "INSERT INTO oauth_connections (org_id, provider, external_workspace_id, "
            "access_token_encrypted) VALUES (%s::uuid, 'notion', 'ws', 'x') "
            "RETURNING id::text",
            (org_id,),
        ).fetchone()[0]
    job_id = queue.enqueue(org_id, connection_id)
    queue.claim_next()

    # Advancing only the counter must still stamp progress_at — otherwise a job
    # in a long single phase looks silent to the reaper.
    queue.update_progress(job_id, processed=3)
    with get_connection() as conn:
        progress_at = conn.execute(
            "SELECT progress_at FROM ingestion_jobs WHERE id = %s::uuid", (job_id,)
        ).fetchone()[0]
    assert progress_at is not None
