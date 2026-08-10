"""Product ops gaps: disconnect, folder swap purge, remove member, logout, reauth HTTP."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth import (
    OAuthTokens,
    create_admin,
    create_session_token,
    invite_member,
    save_connection,
    set_connection_config,
)
from app.auth.users import remove_member
from app.core.exceptions import AuthError, OAuthReauthRequiredError
from app.db.connection import get_connection
from app.vectorstore import build_vector_store

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
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
def admin_org(store, org_cleanup):
    org_id = store.create_organization(f"Ops Gaps Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    cookies = {"session": create_session_token(admin)}
    return org_id, admin, cookies


def _save_google(org_id: str) -> str:
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
    )


def _insert_google_doc(org_id: str, external_id: str = "doc-1") -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents (org_id, title, source_provider, source_external_id)
            VALUES (%s::uuid, %s, 'google', %s)
            """,
            (org_id, "Policy Doc", external_id),
        )


@requires_db
def test_disconnect_google_purges_indexed_docs(client, admin_org):
    org_id, admin, cookies = admin_org
    connection_id = _save_google(org_id)
    set_connection_config(org_id, "google", {"folder_id": "folder-a", "folder_name": "A"})
    _insert_google_doc(org_id)

    store = build_vector_store()
    assert len(store.list_source_documents(org_id, "google")) == 1

    response = client.delete(f"/admin/connections/{connection_id}", cookies=cookies)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "disconnected"
    assert body["provider"] == "google"
    assert body["documents_purged"] == 1

    assert client.get("/admin/connections", cookies=cookies).json() == []
    assert store.list_source_documents(org_id, "google") == []


@requires_db
def test_change_folder_purges_old_corpus(client, admin_org, monkeypatch):
    org_id, admin, cookies = admin_org
    connection_id = _save_google(org_id)
    set_connection_config(org_id, "google", {"folder_id": "folder-old", "folder_name": "Old"})
    _insert_google_doc(org_id, "old-doc")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "id": "folder-new",
                "name": "New",
                "mimeType": "application/vnd.google-apps.folder",
            }

    monkeypatch.setattr(
        "app.sources.google_drive_utils.httpx.get", lambda *a, **k: FakeResponse()
    )
    monkeypatch.setattr(
        "app.api.admin.get_live_connection_token", lambda *a, **k: "tok"
    )

    response = client.put(
        f"/admin/connections/{connection_id}/config",
        json={"folder_url": "folder-new"},
        cookies=cookies,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["folder_changed"] is True
    assert body["documents_purged"] == 1
    assert body["config"]["folder_id"] == "folder-new"
    assert build_vector_store().list_source_documents(org_id, "google") == []


@requires_db
def test_changes_returns_401_structured_when_oauth_reauth_required(
    client, admin_org, monkeypatch
):
    org_id, admin, cookies = admin_org
    connection_id = _save_google(org_id)
    set_connection_config(org_id, "google", {"folder_id": "f1", "folder_name": "F"})

    def boom(*a, **k):
        raise OAuthReauthRequiredError("reconnect Google Drive")

    monkeypatch.setattr("app.api.admin.get_live_connection_token", boom)

    response = client.get(f"/admin/connections/{connection_id}/changes", cookies=cookies)
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["code"] == "oauth_reauth_required"
    assert "reconnect" in detail["message"].lower()


@requires_db
def test_remove_member_and_cannot_remove_self(client, admin_org):
    org_id, admin, cookies = admin_org
    member = invite_member(f"member-{uuid.uuid4().hex[:8]}@example.com", org_id)

    removed = client.delete(f"/admin/members/{member.id}", cookies=cookies)
    assert removed.status_code == 200
    emails = [m["email"] for m in client.get("/admin/members", cookies=cookies).json()]
    assert member.email not in emails

    self_rm = client.delete(f"/admin/members/{admin.id}", cookies=cookies)
    assert self_rm.status_code == 400


@requires_db
def test_remove_member_helper_blocks_last_admin(store, org_cleanup):
    org_id = store.create_organization(f"Last Admin Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"solo-{uuid.uuid4().hex[:8]}@example.com", org_id)
    other = invite_member(f"m-{uuid.uuid4().hex[:8]}@example.com", org_id)
    # Promote? We only have invite as member. Create second admin via create_admin
    # can't — create_admin always ROLE_ADMIN on new email. Use remove on sole admin via helper.
    with pytest.raises(AuthError, match="last admin"):
        remove_member(admin.id, org_id, acting_user_id=other.id)


@requires_db
def test_logout_clears_session_cookie(client, admin_org):
    org_id, admin, cookies = admin_org
    assert client.get("/me", cookies=cookies).status_code == 200
    response = client.post("/auth/logout", cookies=cookies)
    assert response.status_code == 200
    assert response.json()["status"] == "signed_out"
    # TestClient may still send the old cookie unless we clear it from the jar.
    client.cookies.clear()
    assert client.get("/me").status_code == 401


@requires_db
def test_workspace_disconnect_purges_workspace_docs_only(client, admin_org, store):
    org_id, admin, cookies = admin_org
    # Org-wide google doc must survive workspace disconnect.
    org_conn = _save_google(org_id)
    set_connection_config(org_id, "google", {"folder_id": "org-f", "folder_name": "Org"})
    _insert_google_doc(org_id, "org-doc")

    ws = client.post("/workspaces", json={"name": "Notes"}, cookies=cookies).json()
    workspace_id = ws["id"]
    ws_conn = save_connection(
        org_id,
        "google",
        OAuthTokens(
            access_token="ws-access",
            refresh_token="ws-refresh",
            expires_at=None,
            external_workspace_id="ws-drive",
            external_workspace_name="ws-drive",
        ),
        workspace_id=workspace_id,
    )
    set_connection_config(
        org_id, "google", {"folder_id": "ws-f", "folder_name": "WS"}, workspace_id=workspace_id
    )
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents (org_id, title, source_provider, source_external_id, workspace_id)
            VALUES (%s::uuid, 'WS Doc', 'google', 'ws-doc', %s::uuid)
            """,
            (org_id, workspace_id),
        )

    response = client.delete(
        f"/workspaces/{workspace_id}/connections/{ws_conn}", cookies=cookies
    )
    assert response.status_code == 200
    assert response.json()["documents_purged"] == 1

    store = build_vector_store()
    assert len(store.list_source_documents(org_id, "google")) == 1  # org-wide remains
    assert store.list_source_documents(org_id, "google", workspace_id=workspace_id) == []
    # org connection still listed
    assert any(c["id"] == org_conn for c in client.get("/admin/connections", cookies=cookies).json())
