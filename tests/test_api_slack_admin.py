"""Phase 4 (Slack Integration Plan): admin channel-picker HTTP routes.

Mirrors tests/test_api_admin.py's Google Drive config test pattern —
``@requires_db`` (needs a real Postgres, ``docker compose up -d``), httpx
monkeypatched at the Slack call sites so no real Slack API is hit.
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
def admin_org(store, org_cleanup):
    org_id = store.create_organization(f"Slack Admin API Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    token = create_session_token(admin)
    return org_id, {"session": token}


def _save_slack(org_id: str, *, workspace: str = "T0123ABC") -> str:
    return save_connection(
        org_id,
        "slack",
        OAuthTokens(
            access_token="xoxb-abc",
            refresh_token=None,
            expires_at=None,
            external_workspace_id=workspace,
            external_workspace_name="Acme Corp",
        ),
    )


def _fake_slack_response(payload):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    return FakeResponse()


@requires_db
def test_list_slack_channels_returns_bot_visible_channels(client, admin_org, monkeypatch):
    org_id, cookies = admin_org
    connection_id = _save_slack(org_id)

    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.get",
        lambda *a, **k: _fake_slack_response(
            {
                "ok": True,
                "channels": [
                    {"id": "C1", "name": "general", "is_private": False, "is_member": True},
                    {"id": "C2", "name": "eng-private", "is_private": True, "is_member": False},
                ],
            }
        ),
    )

    response = client.get(f"/admin/connections/{connection_id}/slack-channels", cookies=cookies)
    assert response.status_code == 200
    channels = response.json()["channels"]
    assert {c["id"] for c in channels} == {"C1", "C2"}


@requires_db
def test_list_slack_channels_rejects_non_slack_connection(client, admin_org):
    org_id, cookies = admin_org
    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_x", refresh_token=None, expires_at=None, external_workspace_id="ws"
        ),
    )
    response = client.get(f"/admin/connections/{connection_id}/slack-channels", cookies=cookies)
    assert response.status_code == 400


@requires_db
def test_put_slack_config_saves_selected_channels(client, admin_org, monkeypatch):
    org_id, cookies = admin_org
    connection_id = _save_slack(org_id)

    def fake_get(*a, **k):
        return _fake_slack_response(
            {
                "ok": True,
                "channels": [
                    {"id": "C1", "name": "general", "is_private": False, "is_member": True},
                    {"id": "C2", "name": "eng", "is_private": False, "is_member": False},
                ],
            }
        )

    joined = []

    def fake_post(url, *, data=None, **k):
        joined.append(data["channel"])
        return _fake_slack_response({"ok": True})

    monkeypatch.setattr("app.sources.slack_utils.httpx.get", fake_get)
    monkeypatch.setattr("app.sources.slack_utils.httpx.post", fake_post)

    response = client.put(
        f"/admin/connections/{connection_id}/config",
        json={"channel_ids": ["C1", "C2"]},
        cookies=cookies,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["config"]["channel_ids"] == ["C1", "C2"]
    assert body["config"]["channel_names"] == {"C1": "general", "C2": "eng"}
    # C2 is public and wasn't a member yet -> auto-joined (decision D7).
    assert joined == ["C2"]

    got = client.get(f"/admin/connections/{connection_id}/config", cookies=cookies).json()
    assert got["config"]["channel_ids"] == ["C1", "C2"]


@requires_db
def test_put_slack_config_rejects_unknown_channel(client, admin_org, monkeypatch):
    org_id, cookies = admin_org
    connection_id = _save_slack(org_id)

    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.get",
        lambda *a, **k: _fake_slack_response(
            {"ok": True, "channels": [{"id": "C1", "name": "general", "is_private": False, "is_member": True}]}
        ),
    )

    response = client.put(
        f"/admin/connections/{connection_id}/config",
        json={"channel_ids": ["C1", "C-nope"]},
        cookies=cookies,
    )
    assert response.status_code == 400


@requires_db
def test_put_slack_config_purges_only_on_dropped_channel(client, admin_org, monkeypatch):
    """Adding a channel to an existing selection is not a purge-worthy change;
    dropping one is (decision: mirrors the Drive folder-swap purge)."""
    org_id, cookies = admin_org
    connection_id = _save_slack(org_id)

    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.get",
        lambda *a, **k: _fake_slack_response(
            {
                "ok": True,
                "channels": [
                    {"id": "C1", "name": "general", "is_private": False, "is_member": True},
                    {"id": "C2", "name": "eng", "is_private": False, "is_member": True},
                ],
            }
        ),
    )
    monkeypatch.setattr(
        "app.sources.slack_utils.httpx.post", lambda *a, **k: _fake_slack_response({"ok": True})
    )

    first = client.put(
        f"/admin/connections/{connection_id}/config",
        json={"channel_ids": ["C1", "C2"]},
        cookies=cookies,
    )
    assert first.json()["channels_changed"] is False  # first-time set, nothing to drop

    # Add nothing new, keep both -> not a change.
    second = client.put(
        f"/admin/connections/{connection_id}/config",
        json={"channel_ids": ["C1", "C2"]},
        cookies=cookies,
    )
    assert second.json()["channels_changed"] is False

    # Drop C2 -> a real change, purge fires.
    third = client.put(
        f"/admin/connections/{connection_id}/config",
        json={"channel_ids": ["C1"]},
        cookies=cookies,
    )
    assert third.json()["channels_changed"] is True
