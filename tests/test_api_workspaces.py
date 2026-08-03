"""Task 9: /workspaces HTTP API (create, invite, connections, jobs).

Focus: every route requires session auth; every route on an EXISTING
workspace additionally requires membership (and owner role where the plan
restricts it), and a workspace from one org must never resolve for a
different org's session, even by guessing its id -- the single most
important property this file proves.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import OAuthTokens, create_admin, create_session_token, save_connection
from app.auth.users import invite_member as invite_org_member

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("EMAIL_SENDER", "console")
    monkeypatch.setenv("FRONTEND_URL", "https://portal.example.com")
    monkeypatch.setenv("API_CORS_ORIGINS", "https://portal.example.com")


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


@pytest.fixture
def owner_org(store, org_cleanup):
    """(org_id, owner_user, session_cookie_dict) for a fresh org's admin."""
    org_id = store.create_organization(f"Workspace API Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    token = create_session_token(owner)
    return org_id, owner, {"session": token}


@requires_db
def test_create_workspace_and_creator_is_owner(client, owner_org):
    org_id, owner, cookies = owner_org
    response = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=cookies)
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "owner"
    workspace_id = body["id"]

    members = client.get(f"/workspaces/{workspace_id}/members", cookies=cookies)
    assert members.status_code == 200
    assert [m["email"] for m in members.json()] == [owner.email]


@requires_db
def test_list_mine_only_returns_workspaces_i_am_a_member_of(client, store, org_cleanup):
    org_id = store.create_organization(f"Workspace List Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    alice = create_admin(f"alice-{uuid.uuid4().hex[:8]}@example.com", org_id)
    bob = create_admin(f"bob-{uuid.uuid4().hex[:8]}@example.com", org_id)
    alice_cookies = {"session": create_session_token(alice)}
    bob_cookies = {"session": create_session_token(bob)}

    client.post("/workspaces", json={"name": "Alice's Workspace"}, cookies=alice_cookies)

    alice_list = client.get("/workspaces", cookies=alice_cookies)
    bob_list = client.get("/workspaces", cookies=bob_cookies)
    assert len(alice_list.json()) == 1
    assert bob_list.json() == []


@requires_db
def test_invite_requires_owner_role(client, store, org_cleanup):
    org_id = store.create_organization(f"Workspace Invite Role Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    owner_cookies = {"session": create_session_token(owner)}
    colleague = invite_org_member(f"colleague-{uuid.uuid4().hex[:8]}@example.com", org_id)

    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=owner_cookies)
    workspace_id = create_resp.json()["id"]
    client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": colleague.email},
        cookies=owner_cookies,
    )

    colleague_cookies = {"session": create_session_token(colleague)}
    second_invite = invite_org_member(f"second-{uuid.uuid4().hex[:8]}@example.com", org_id)
    response = client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": second_invite.email},
        cookies=colleague_cookies,
    )
    assert response.status_code == 403


@requires_db
def test_invite_rejects_email_outside_the_org(client, owner_org):
    org_id, owner, cookies = owner_org
    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=cookies)
    workspace_id = create_resp.json()["id"]

    response = client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": "not-in-this-org@example.com"},
        cookies=cookies,
    )
    assert response.status_code == 404


@requires_db
def test_non_member_gets_403_on_members_route(client, store, org_cleanup):
    org_id = store.create_organization(f"Workspace Non-Member Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    stranger = create_admin(f"stranger-{uuid.uuid4().hex[:8]}@example.com", org_id)
    owner_cookies = {"session": create_session_token(owner)}
    stranger_cookies = {"session": create_session_token(stranger)}

    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=owner_cookies)
    workspace_id = create_resp.json()["id"]

    response = client.get(f"/workspaces/{workspace_id}/members", cookies=stranger_cookies)
    assert response.status_code == 403


@requires_db
def test_workspace_from_another_org_never_resolves(client, store, org_cleanup):
    """The critical cross-tenant proof: org B's admin, given org A's real
    workspace id, must get 403 -- never 200 with data, never a 500."""
    org_a = store.create_organization(f"Workspace Cross Org A {uuid.uuid4().hex[:8]}")
    org_b = store.create_organization(f"Workspace Cross Org B {uuid.uuid4().hex[:8]}")
    org_cleanup.extend([org_a, org_b])
    owner_a = create_admin(f"owner-a-{uuid.uuid4().hex[:8]}@example.com", org_a)
    admin_b = create_admin(f"admin-b-{uuid.uuid4().hex[:8]}@example.com", org_b)
    owner_a_cookies = {"session": create_session_token(owner_a)}
    admin_b_cookies = {"session": create_session_token(admin_b)}

    create_resp = client.post(
        "/workspaces", json={"name": "Org A Meeting Notes"}, cookies=owner_a_cookies
    )
    workspace_id = create_resp.json()["id"]

    for path in (
        f"/workspaces/{workspace_id}/members",
        f"/workspaces/{workspace_id}/connections",
        f"/workspaces/{workspace_id}/jobs",
    ):
        response = client.get(path, cookies=admin_b_cookies)
        assert response.status_code == 403, f"{path} leaked across orgs: {response.status_code}"


@requires_db
def test_workspaces_route_requires_a_session(client, owner_org):
    org_id, owner, cookies = owner_org
    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=cookies)
    workspace_id = create_resp.json()["id"]

    response = client.get(f"/workspaces/{workspace_id}/members")
    assert response.status_code == 401


# -- Drive folder-picker dropdown (search-as-you-type over an existing connection) --


def _save_workspace_google(org_id: str, workspace_id: str) -> str:
    return save_connection(
        org_id,
        "google",
        OAuthTokens(
            access_token="goog_access",
            refresh_token="goog_refresh",
            expires_at=None,
            external_workspace_id="drive-user@example.com",
            external_workspace_name="drive-user@example.com",
        ),
        workspace_id=workspace_id,
    )


@requires_db
def test_search_workspace_drive_folders_returns_matches(client, owner_org, monkeypatch):
    org_id, owner, cookies = owner_org
    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=cookies)
    workspace_id = create_resp.json()["id"]
    connection_id = _save_workspace_google(org_id, workspace_id)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"files": [{"id": "folder-1", "name": "Q3 Meeting Notes"}]}

    monkeypatch.setattr(
        "app.sources.google_drive_utils.httpx.get", lambda *a, **k: FakeResponse()
    )

    response = client.get(
        f"/workspaces/{workspace_id}/connections/{connection_id}/drive-folders",
        params={"q": "Q3"},
        cookies=cookies,
    )
    assert response.status_code == 200
    assert response.json()["folders"] == [{"id": "folder-1", "name": "Q3 Meeting Notes"}]


@requires_db
def test_search_workspace_drive_folders_requires_owner_role(client, store, org_cleanup):
    org_id = store.create_organization(f"Workspace Drive Search Role Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    owner_cookies = {"session": create_session_token(owner)}
    colleague = invite_org_member(f"colleague-{uuid.uuid4().hex[:8]}@example.com", org_id)
    colleague_cookies = {"session": create_session_token(colleague)}

    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=owner_cookies)
    workspace_id = create_resp.json()["id"]
    client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": colleague.email},
        cookies=owner_cookies,
    )
    connection_id = _save_workspace_google(org_id, workspace_id)

    response = client.get(
        f"/workspaces/{workspace_id}/connections/{connection_id}/drive-folders",
        cookies=colleague_cookies,
    )
    assert response.status_code == 403


@requires_db
def test_search_workspace_drive_folders_wrong_workspace_returns_404(client, owner_org):
    org_id, owner, cookies = owner_org
    workspace_a = client.post(
        "/workspaces", json={"name": "Workspace A"}, cookies=cookies
    ).json()["id"]
    workspace_b = client.post(
        "/workspaces", json={"name": "Workspace B"}, cookies=cookies
    ).json()["id"]
    connection_id = _save_workspace_google(org_id, workspace_a)

    response = client.get(
        f"/workspaces/{workspace_b}/connections/{connection_id}/drive-folders",
        cookies=cookies,
    )
    assert response.status_code == 404
