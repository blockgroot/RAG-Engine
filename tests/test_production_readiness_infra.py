"""Readiness, statement timeouts and the schema lock (production-readiness audit)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config.settings import DatabaseSettings


def test_ready_is_503_when_the_database_does_not_answer(monkeypatch):
    """/health stayed 200 while every chat failed on a dead database."""
    def no_pool(*a, **k):
        raise RuntimeError("pool exhausted")
    monkeypatch.setattr("app.db.connection.get_pool", no_pool)
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["database"] == "down"


def test_database_limits_have_safe_defaults_and_read_the_env(monkeypatch):
    for name in ("DB_STATEMENT_TIMEOUT_SECONDS", "DB_IDLE_IN_TRANSACTION_SECONDS",
                 "DB_POOL_WAIT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    s = DatabaseSettings.from_env()
    assert s.statement_timeout_seconds == 120
    assert s.idle_in_transaction_seconds == 300
    assert s.pool_wait_seconds == 15.0
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_SECONDS", "0")
    assert DatabaseSettings.from_env().statement_timeout_seconds == 0


from .conftest import requires_db  # noqa: E402


@requires_db
def test_every_pooled_connection_is_bounded_server_side():
    from app.db.connection import get_connection

    with get_connection() as conn:
        assert conn.execute("SHOW statement_timeout").fetchone()[0] == "2min"
        assert conn.execute("SHOW idle_in_transaction_session_timeout").fetchone()[0] == "5min"


def test_every_response_carries_a_request_id_and_a_safe_one_is_kept():
    client = TestClient(create_app())
    made = client.get("/health").headers["x-request-id"]
    assert len(made) == 16
    assert client.get("/health", headers={"X-Request-ID": "abc-123"}).headers[
        "x-request-id"] == "abc-123"
    forged = client.get("/health", headers={"X-Request-ID": "x\nERROR fake"}).headers[
        "x-request-id"]
    assert "\n" not in forged and forged != "x\nERROR fake"


def test_log_lines_carry_the_request_id():
    import logging

    from app.core.logs import REQUEST_ID, _RequestIdFilter

    record = logging.LogRecord("t", logging.INFO, __file__, 1, "m", None, None)
    token = REQUEST_ID.set("req-9")
    try:
        _RequestIdFilter().filter(record)
    finally:
        REQUEST_ID.reset(token)
    assert record.request_id == "req-9"
