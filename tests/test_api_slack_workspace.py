"""Phase 4/5 (Slack Integration Plan): workspace-scoped Slack channel picker
+ decision D10 (a channel can only be claimed by ONE connection at a time,
org-wide or a specific workspace).

Mirrors tests/test_api_workspaces.py's fixtures/patterns.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import OAuthTokens, create_admin, create_session_token, save_connection

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
    org_id = store.create_organization(f"Slack Workspace API Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    token = create_session_token(owner)
    return org_id, {"session": token}


def _fake_slack_response(payload):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    return FakeResponse()


def _channels_payload(*channels):
    return {
        "ok": True,
        "channels": [
            {"id": cid, "name": name, "is_private": False, "is_member": True}
            for cid, name in channels
        ],
    }


@requires_db
def test_workspace_slack_channel_picker_saves_scoped_to_workspace(client, owner_org, monkeypatch):
    org_id, cookies = owner_org
    workspace_id = client.post(
        "/workspaces", json={"name": "Meeting Notes"}, cookies=cookies
    ).json()["id"]
    connection_id = save_connection(
        org_id,
        "slack",
        OAuthTokens(
            access_token="xoxb-ws", refresh_token=None, expires_at=None, external_workspace_id="T1"
        ),
        workspace_id=workspace_id,
    )

    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.get",
        lambda *a, **k: _fake_slack_response(_channels_payload(("C1", "team-planning"))),
    )
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.post", lambda *a, **k: _fake_slack_response({"ok": True})
    )

    response = client.put(
        f"/workspaces/{workspace_id}/connections/{connection_id}/config",
        json={"channel_ids": ["C1"]},
        cookies=cookies,
    )
    assert response.status_code == 200
    assert response.json()["config"]["channel_ids"] == ["C1"]

    # Never leaks into the org-wide connection list.
    org_wide = client.get("/admin/connections", cookies=cookies).json()
    assert org_wide == []


@requires_db
def test_decision_d10_rejects_channel_already_claimed_org_wide(client, owner_org, monkeypatch):
    """A channel already connected under Company Sources can't also be
    claimed by a personal workspace (decision D10)."""
    org_id, cookies = owner_org

    org_connection_id = save_connection(
        org_id,
        "slack",
        OAuthTokens(
            access_token="xoxb-org", refresh_token=None, expires_at=None, external_workspace_id="T1"
        ),
    )
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.get",
        lambda *a, **k: _fake_slack_response(_channels_payload(("C1", "general"))),
    )
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.post", lambda *a, **k: _fake_slack_response({"ok": True})
    )
    org_put = client.put(
        f"/admin/connections/{org_connection_id}/config",
        json={"channel_ids": ["C1"]},
        cookies=cookies,
    )
    assert org_put.status_code == 200

    workspace_id = client.post(
        "/workspaces", json={"name": "Personal Space"}, cookies=cookies
    ).json()["id"]
    ws_connection_id = save_connection(
        org_id,
        "slack",
        OAuthTokens(
            access_token="xoxb-ws2", refresh_token=None, expires_at=None, external_workspace_id="T1"
        ),
        workspace_id=workspace_id,
    )

    conflict = client.put(
        f"/workspaces/{workspace_id}/connections/{ws_connection_id}/config",
        json={"channel_ids": ["C1"]},
        cookies=cookies,
    )
    assert conflict.status_code == 400


@requires_db
def test_decision_d10_rejects_channel_already_claimed_by_sibling_workspace(
    client, owner_org, monkeypatch
):
    org_id, cookies = owner_org
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.get",
        lambda *a, **k: _fake_slack_response(_channels_payload(("C1", "general"))),
    )
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.post", lambda *a, **k: _fake_slack_response({"ok": True})
    )

    ws1 = client.post("/workspaces", json={"name": "Space One"}, cookies=cookies).json()["id"]
    conn1 = save_connection(
        org_id,
        "slack",
        OAuthTokens(
            access_token="xoxb-ws1", refresh_token=None, expires_at=None, external_workspace_id="T1"
        ),
        workspace_id=ws1,
    )
    first = client.put(
        f"/workspaces/{ws1}/connections/{conn1}/config",
        json={"channel_ids": ["C1"]},
        cookies=cookies,
    )
    assert first.status_code == 200

    ws2 = client.post("/workspaces", json={"name": "Space Two"}, cookies=cookies).json()["id"]
    conn2 = save_connection(
        org_id,
        "slack",
        OAuthTokens(
            access_token="xoxb-ws2", refresh_token=None, expires_at=None, external_workspace_id="T1"
        ),
        workspace_id=ws2,
    )
    conflict = client.put(
        f"/workspaces/{ws2}/connections/{conn2}/config",
        json={"channel_ids": ["C1"]},
        cookies=cookies,
    )
    assert conflict.status_code == 400
