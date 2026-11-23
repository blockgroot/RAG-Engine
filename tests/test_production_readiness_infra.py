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


@requires_db
def test_delete_company_needs_the_exact_name_then_removes_everything(store, org_cleanup):
    import importlib.util
    import uuid
    from pathlib import Path

    from app.db.connection import get_connection

    spec = importlib.util.spec_from_file_location(
        "delete_company", Path(__file__).parent.parent / "scripts" / "delete_company.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    name = f"Delete Me {uuid.uuid4().hex[:8]}"
    org_id = store.create_organization(name)
    org_cleanup.append(org_id)

    deleted, message = module.delete_company(org_id, "delete me")
    assert not deleted and "exact name" in message
    deleted, _ = module.delete_company(org_id, name)
    assert deleted
    with get_connection(all_orgs=True) as conn:
        assert conn.execute(
            "SELECT 1 FROM organizations WHERE id = %s::uuid", (org_id,)).fetchone() is None


def test_boot_fails_on_missing_or_broken_secrets(monkeypatch, capsys):
    import importlib.util
    from pathlib import Path

    from cryptography.fernet import Fernet

    spec = importlib.util.spec_from_file_location(
        "boot", Path(__file__).parent.parent / "scripts" / "boot.py")
    boot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boot)

    monkeypatch.setenv("AUTH_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    assert boot.check_secrets() == []

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", "not-a-key")
    assert any("AUTH_ENCRYPTION_KEYS" in p for p in boot.check_secrets())

    monkeypatch.delenv("AUTH_JWT_SECRET")
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    assert any("AUTH_JWT_SECRET" in p for p in boot.check_secrets())

    monkeypatch.setenv("AUTH_JWT_SECRET", "short")
    assert boot.check_secrets() == []
    assert "shorter than 32" in capsys.readouterr().out


@requires_db
def test_export_company_writes_its_rows_and_never_a_credential(store, org_cleanup, tmp_path):
    import importlib.util
    import json
    import uuid
    from pathlib import Path

    from app.db.connection import get_connection

    spec = importlib.util.spec_from_file_location(
        "export_company", Path(__file__).parent.parent / "scripts" / "export_company.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    org_id = store.create_organization(f"Export {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    other = store.create_organization(f"Other {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other)
    with get_connection() as conn:
        for org in (org_id, other):
            conn.execute(
                "INSERT INTO oauth_connections (org_id, provider, external_workspace_id, "
                "access_token_encrypted) VALUES (%s::uuid, 'notion', %s, 'SECRET')",
                (org, f"ws-{org}"),
            )
    counts = module.export_company(org_id, tmp_path)
    assert counts["organizations"] == 1 and counts["oauth_connections"] == 1
    text = (tmp_path / "oauth_connections.jsonl").read_text()
    row = json.loads(text)
    assert row["org_id"] == org_id and "access_token_encrypted" not in row
    assert "SECRET" not in text and other not in text
