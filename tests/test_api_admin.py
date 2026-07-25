"""Phase 13c: admin router (members, connections, jobs).

Focus of these tests: every endpoint is admin-only AND scoped to the caller's
own org_id — a second org's admin must never see or act on the first org's
members/connections/jobs, even by guessing an id.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import OAuthTokens, create_admin, create_session_token, save_connection
from app.jobs import enqueue

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("FRONTEND_URL", "https://portal.example.com")
    monkeypatch.setenv("API_CORS_ORIGINS", "https://portal.example.com")


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


@pytest.fixture
def admin_org(store, org_cleanup):
    """(org_id, session_cookie_dict) for a fresh org's admin."""
    org_id = store.create_organization(f"Admin API Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    token = create_session_token(admin)
    return org_id, {"session": token}


@requires_db
def test_admin_routes_require_a_session(client):
    assert client.get("/admin/members").status_code == 401
    assert client.get("/admin/connections").status_code == 401
    assert client.get("/admin/jobs").status_code == 401


@requires_db
def test_invite_and_list_member(client, admin_org):
    org_id, cookies = admin_org
    email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"

    response = client.post("/admin/members", json={"email": email}, cookies=cookies)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == email
    assert body["role"] == "member"

    listed = client.get("/admin/members", cookies=cookies).json()
    emails = {m["email"] for m in listed}
    assert email in emails
    # The admin themself is also a member of the list.
    assert len(listed) == 2


@requires_db
def test_invite_rejects_malformed_email(client, admin_org):
    _, cookies = admin_org
    response = client.post("/admin/members", json={"email": "not-an-email"}, cookies=cookies)
    assert response.status_code == 400


@requires_db
def test_invite_rejects_email_that_already_has_an_account(client, admin_org, store, org_cleanup):
    org_id, cookies = admin_org
    email = f"dup-{uuid.uuid4().hex[:8]}@example.com"
    first = client.post("/admin/members", json={"email": email}, cookies=cookies)
    assert first.status_code == 200

    other_org = store.create_organization(f"Admin API Invite Other Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other_org)
    other_admin = create_admin(f"other-admin-{uuid.uuid4().hex[:8]}@example.com", other_org)
    other_cookies = {"session": create_session_token(other_admin)}

    second = client.post("/admin/members", json={"email": email}, cookies=other_cookies)
    assert second.status_code == 400


@requires_db
def test_members_list_scoped_to_own_org(client, admin_org, store, org_cleanup):
    org_id, cookies = admin_org
    other_org = store.create_organization(f"Admin API Members Other Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other_org)
    other_admin = create_admin(f"other-admin2-{uuid.uuid4().hex[:8]}@example.com", other_org)
    other_cookies = {"session": create_session_token(other_admin)}
    client.post(
        "/admin/members",
        json={"email": f"other-member-{uuid.uuid4().hex[:8]}@example.com"},
        cookies=other_cookies,
    )

    listed = client.get("/admin/members", cookies=cookies).json()
    assert all(m["email"] != other_admin.email for m in listed)


@requires_db
def test_connections_list_scoped_to_own_org(client, admin_org, store, org_cleanup):
    org_id, cookies = admin_org
    save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_admin_api",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-admin-api",
            external_workspace_name="Admin API Workspace",
        ),
    )

    other_org = store.create_organization(f"Admin API Other Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other_org)
    save_connection(
        other_org,
        "notion",
        OAuthTokens(
            access_token="ntn_other",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-other",
        ),
    )

    connections = client.get("/admin/connections", cookies=cookies).json()
    assert len(connections) == 1
    assert connections[0]["external_workspace_name"] == "Admin API Workspace"


@requires_db
def test_trigger_ingest_rejects_another_orgs_connection_id(
    client, admin_org, store, org_cleanup
):
    org_id, cookies = admin_org
    other_org = store.create_organization(f"Admin API Other Org 2 {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other_org)
    other_connection_id = save_connection(
        other_org,
        "notion",
        OAuthTokens(
            access_token="ntn_other2",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-other2",
        ),
    )

    response = client.post(
        f"/admin/connections/{other_connection_id}/ingest", cookies=cookies
    )
    assert response.status_code == 404


@requires_db
def test_trigger_ingest_and_poll_job(client, admin_org):
    org_id, cookies = admin_org
    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_admin_api2",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-admin-api2",
        ),
    )

    response = client.post(f"/admin/connections/{connection_id}/ingest", cookies=cookies)
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    job = client.get(f"/admin/jobs/{job_id}", cookies=cookies).json()
    assert job["status"] == "queued"

    jobs = client.get("/admin/jobs", cookies=cookies).json()
    assert any(j["id"] == job_id for j in jobs)


@requires_db
def test_job_lookup_scoped_to_own_org(client, admin_org, store, org_cleanup):
    org_id, cookies = admin_org
    other_org = store.create_organization(f"Admin API Other Org 3 {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other_org)
    other_connection_id = save_connection(
        other_org,
        "notion",
        OAuthTokens(
            access_token="ntn_other3",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-other3",
        ),
    )
    other_job_id = enqueue(other_org, other_connection_id)

    response = client.get(f"/admin/jobs/{other_job_id}", cookies=cookies)
    assert response.status_code == 404

    jobs = client.get("/admin/jobs", cookies=cookies).json()
    assert not any(j["id"] == other_job_id for j in jobs)


@requires_db
def test_non_admin_cannot_access_admin_routes(client, store, org_cleanup):
    org_id = store.create_organization(f"Admin API Non-Admin Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    member = create_admin(f"member-{uuid.uuid4().hex[:8]}@example.com", org_id)

    from app.db.connection import get_connection

    with get_connection() as conn:
        conn.execute("UPDATE users SET role = 'member' WHERE id = %s", (member.id,))
    from app.auth.users import get_user

    token = create_session_token(get_user(member.id))

    response = client.get("/admin/members", cookies={"session": token})
    assert response.status_code == 403
