"""The externally-driven background tick.

This endpoint is what keeps freshness working on a free-tier host that spins
down after ~15 minutes with no inbound request. It is also unauthenticated by
session (a cron job has no org), so its secret check is a real access boundary
and gets tested like one: an open tick is a free way for anyone to spend every
tenant's provider quota.

No DB needed — the tick's steps are stubbed. What is under test is the gate.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

SECRET = "tick-secret-do-not-use-in-prod"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("API_CORS_ORIGINS", "https://portal.example.com")
    # The worker thread would poll a DB we are not providing.
    monkeypatch.setenv("INGEST_WORKER_IN_API", "false")


@pytest.fixture
def client(monkeypatch):
    """A client whose tick does no real work — the gate is the subject."""
    import app.api.internal as internal

    monkeypatch.setattr(
        internal,
        "run_external_tick",
        lambda: {"reaped": 0, "syncs_queued": 2, "schedulers_ran": 1},
    )
    from app.api.main import create_app

    return TestClient(create_app())


def test_no_secret_configured_hides_the_endpoint(client, monkeypatch):
    """404, not 403: an unconfigured tick should not advertise that it exists
    and is merely locked. Unset means OFF, never open."""
    monkeypatch.delenv("INTERNAL_TICK_SECRET", raising=False)
    assert client.post("/internal/tick").status_code == 404


def test_a_wrong_secret_is_refused(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_TICK_SECRET", SECRET)
    resp = client.post("/internal/tick", headers={"X-Tick-Secret": "wrong"})
    assert resp.status_code == 403


def test_a_missing_header_is_refused(client, monkeypatch):
    """Absent must fail closed, not fall through to a None == None comparison."""
    monkeypatch.setenv("INTERNAL_TICK_SECRET", SECRET)
    assert client.post("/internal/tick").status_code == 403


def test_the_right_secret_runs_the_tick_and_reports_what_it_did(client, monkeypatch):
    """Counts come back so the cron's own log shows whether the tick did
    anything — a 200 with no body would make a silently broken tick look
    healthy for weeks."""
    monkeypatch.setenv("INTERNAL_TICK_SECRET", SECRET)
    resp = client.post("/internal/tick", headers={"X-Tick-Secret": SECRET})
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "reaped": 0,
        "syncs_queued": 2,
        "schedulers_ran": 1,
    }


def test_get_is_not_allowed(client, monkeypatch):
    """POST only: a cron misconfigured as GET should fail loudly rather than
    look like it is working."""
    monkeypatch.setenv("INTERNAL_TICK_SECRET", SECRET)
    assert client.get("/internal/tick").status_code == 405
