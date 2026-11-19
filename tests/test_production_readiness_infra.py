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
