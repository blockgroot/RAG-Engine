"""Tenant direct access: a company's read-only login sees only its own chunks.

The isolation tests log in AS the tenant's login, over the same address the
tests use, because only a real login proves what that login can and cannot do.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from app.auth import create_admin, create_session_token
from app.config.settings import TenantAccessSettings
from app.db import get_connection
from app.tenantaccess import (
    create_login,
    drop_login,
    get_login,
    hba_lines,
    parse_allowed_ips,
    reset_password,
    set_allowed_ips,
)
from app.tenantaccess.store import scram_verifier

from .conftest import requires_db

VECTOR = [1.0] + [0.0] * 1023
SETTINGS = TenantAccessSettings(replica_host="replica.example.com")


def _login(role_name: str, password: str) -> psycopg.Connection:
    """Connect as the tenant login. Supabase's pooler names a login role.project."""
    base = conninfo_to_dict(os.environ["DATABASE_URL"])
    user = role_name
    if "." in (base.get("user") or ""):
        user = f"{role_name}.{base['user'].split('.', 1)[1]}"
    return psycopg.connect(
        host=base["host"],
        port=base.get("port") or 5432,
        dbname=base.get("dbname") or "postgres",
        user=user,
        password=password,
        sslmode=base.get("sslmode") or "prefer",
        autocommit=True,
    )


@pytest.fixture
def two_orgs(store, org_cleanup):
    orgs = []
    for name in ("reader-a", "reader-b"):
        org = store.create_organization(f"{name}-{uuid.uuid4().hex[:6]}")
        org_cleanup.append(org)
        store.add_document(org, f"{name} doc", [f"{name} secret text"], [VECTOR])
        orgs.append(org)
    yield orgs
    for org in orgs:
        drop_login(org)


# --- no database -----------------------------------------------------------


def test_allowed_ips_are_normalized_and_bounded():
    assert parse_allowed_ips(["203.0.113.7", "198.51.100.9/24", "203.0.113.7"]) == [
        "203.0.113.7/32",
        "198.51.100.0/24",
    ]
    for bad in ([], ["nope"], ["0.0.0.0/0"], ["10.0.0.0/8"], ["2001:db8::/16"], None):
        with pytest.raises(ValueError):
            parse_allowed_ips(bad)


def test_hba_lines_are_ssl_only_per_login_then_reject_other_readers():
    out = hba_lines([("reader_a", ["203.0.113.7/32", "198.51.100.0/24"])], "postgres")
    assert out.splitlines() == [
        "hostssl  postgres  reader_a  203.0.113.7/32  scram-sha-256",
        "hostssl  postgres  reader_a  198.51.100.0/24  scram-sha-256",
        "host     all  +tenant_readers  all  reject",
    ]


def test_the_database_is_given_a_verifier_never_the_password():
    verifier = scram_verifier("hunter2")
    assert verifier.startswith("SCRAM-SHA-256$4096:")
    assert "hunter2" not in verifier


# --- the login itself --------------------------------------------------------


@requires_db
def test_login_sees_only_its_own_organization(two_orgs):
    a, b = two_orgs
    creds = create_login(a, ["203.0.113.0/24"], None, SETTINGS)
    assert creds.uri == (
        f"postgresql://{creds.role_name}:{creds.password}@replica.example.com:5432"
        "/postgres?sslmode=verify-full"
    )
    with _login(creds.role_name, creds.password) as conn:
        assert conn.execute("SELECT org_id::text, content FROM chunks").fetchall() == [
            (a, "reader-a secret text")
        ]
        assert conn.execute(
            "SELECT count(*) FROM chunks WHERE org_id = %s", (b,)
        ).fetchone()[0] == 0
        # The app's own switches are settable by anyone; they must not apply.
        conn.execute("SELECT set_config('app.org_id', %s, false)", (b,))
        conn.execute("SELECT set_config('app.all_orgs', 'on', false)")
        assert conn.execute("SELECT DISTINCT org_id::text FROM chunks").fetchall() == [(a,)]
        assert conn.execute("SELECT org_id::text FROM tenant_reader_logins").fetchall() == [
            (a,)
        ]


@requires_db
def test_login_cannot_write_or_read_anything_else(two_orgs):
    a, _ = two_orgs
    creds = create_login(a, ["203.0.113.0/24"], None, SETTINGS)
    partition = "chunk_partitions.org_" + a.replace("-", "")
    with _login(creds.role_name, creds.password) as conn:
        for sql in (
            "UPDATE chunks SET content = 'x'",
            "DELETE FROM chunks",
            "SELECT count(*) FROM documents",
            "SELECT count(*) FROM organizations",
            "SELECT allowed_ips FROM tenant_reader_logins",
            f"SELECT count(*) FROM {partition}",
            f"SELECT tenant_reader_drop('{a}')",
        ):
            with pytest.raises(psycopg.Error):
                conn.execute(sql)


@requires_db
def test_one_login_per_organization_and_ips_can_change(two_orgs):
    from app.tenantaccess import LoginExistsError

    a, _ = two_orgs
    create_login(a, ["203.0.113.0/24"], None, SETTINGS)
    with pytest.raises(LoginExistsError):
        create_login(a, ["203.0.113.0/24"], None, SETTINGS)
    assert set_allowed_ips(a, ["192.0.2.1"]) is True
    assert get_login(a).allowed_ips == ["192.0.2.1/32"]


@requires_db
def test_reset_password_retires_the_old_one(two_orgs):
    a, _ = two_orgs
    old = create_login(a, ["203.0.113.0/24"], None, SETTINGS)
    new = reset_password(a, SETTINGS)
    with pytest.raises(psycopg.OperationalError):
        _login(old.role_name, old.password).close()
    with _login(new.role_name, new.password) as conn:
        assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 1


@requires_db
def test_revoke_removes_the_login_and_is_idempotent(two_orgs):
    a, _ = two_orgs
    creds = create_login(a, ["203.0.113.0/24"], None, SETTINGS)
    assert drop_login(a) is True
    assert drop_login(a) is False
    assert get_login(a) is None
    with pytest.raises(psycopg.OperationalError):
        _login(creds.role_name, creds.password).close()


@requires_db
def test_deleting_the_organization_drops_its_login(store):
    org = store.create_organization(f"reader-gone-{uuid.uuid4().hex[:6]}")
    creds = create_login(org, ["203.0.113.0/24"], None, SETTINGS)
    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s", (org,))
        role = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (creds.role_name,)
        ).fetchone()
    assert role is None


# --- admin routes ------------------------------------------------------------


@pytest.fixture
def admin(store, org_cleanup, monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("TENANT_ACCESS_REPLICA_HOST", "replica.example.com")
    org = store.create_organization(f"reader-api-{uuid.uuid4().hex[:6]}")
    org_cleanup.append(org)
    user = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org)
    from app.api.main import create_app

    yield org, TestClient(create_app(), cookies={"session": create_session_token(user)})
    drop_login(org)


@requires_db
def test_admin_must_acknowledge_private_documents_and_gets_the_password_once(admin):
    org, client = admin
    body = {"allowed_ips": ["203.0.113.7"]}
    assert client.post("/admin/data-access", json=body).status_code == 400
    r = client.post("/admin/data-access", json={**body, "acknowledge_all_documents": True})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["uri"].startswith("postgresql://reader_")
    shown = client.get("/admin/data-access").json()
    assert shown["login"]["allowed_ips"] == ["203.0.113.7/32"]
    assert "password" not in str(shown)
    assert client.delete("/admin/data-access").json() == {"deleted": True}
    assert client.delete("/admin/data-access").json() == {"deleted": False}


@requires_db
def test_no_replica_configured_means_no_login(admin, monkeypatch):
    _, client = admin
    monkeypatch.delenv("TENANT_ACCESS_REPLICA_HOST")
    r = client.post(
        "/admin/data-access",
        json={"allowed_ips": ["203.0.113.7"], "acknowledge_all_documents": True},
    )
    assert r.status_code == 400
    assert client.get("/admin/data-access").json()["enabled"] is False
