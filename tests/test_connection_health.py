"""Connection health: sticky needs_reauth + auth-shaped SourceError → reconnect."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth import (
    OAuthTokens,
    create_admin,
    create_session_token,
    list_connections,
    save_connection,
)
from app.auth.credentials import (
    clear_needs_reauth,
    looks_like_auth_failure,
    mark_needs_reauth,
)
from app.core.exceptions import OAuthReauthRequiredError, SourceError

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
    org_id = store.create_organization(f"Health Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    cookies = {"session": create_session_token(admin)}
    return org_id, admin, cookies


def _save_notion(org_id: str) -> str:
    return save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_dead",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws",
            external_workspace_name="Acme",
        ),
    )


def test_looks_like_auth_failure_detects_unauthorized_and_skips_generic():
    class Unauthorized(Exception):
        code = "unauthorized"

    assert looks_like_auth_failure(SourceError("fail", cause=Unauthorized()))
    assert looks_like_auth_failure(SourceError("invalid_grant from google"))
    assert not looks_like_auth_failure(SourceError("rate limited, try again"))


@requires_db
def test_list_connections_exposes_sticky_needs_reauth(client, admin_org):
    org_id, _admin, cookies = admin_org
    connection_id = _save_notion(org_id)

    listed = client.get("/admin/connections", cookies=cookies).json()
    assert listed[0]["id"] == connection_id
    assert listed[0]["needs_reauth"] is False

    mark_needs_reauth(org_id, "notion", reason="token revoked")
    listed = client.get("/admin/connections", cookies=cookies).json()
    assert listed[0]["needs_reauth"] is True
    assert "revoked" in (listed[0]["reauth_reason"] or "").lower()


@requires_db
def test_changes_marks_needs_reauth_for_auth_shaped_source_error(
    client, admin_org, monkeypatch
):
    org_id, _admin, cookies = admin_org
    connection_id = _save_notion(org_id)

    class Unauthorized(Exception):
        code = "unauthorized"

    def boom(*_a, **_k):
        raise SourceError("Notion list_documents failed", cause=Unauthorized())

    monkeypatch.setattr("app.api.admin.get_live_connection_token", lambda *a, **k: "tok")
    monkeypatch.setattr("app.api.admin.build_source_adapter", lambda *a, **k: MagicMock())
    monkeypatch.setattr("app.api.admin.detect_source_changes", boom)

    response = client.get(f"/admin/connections/{connection_id}/changes", cookies=cookies)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "oauth_reauth_required"

    info = list_connections(org_id)[0]
    assert info.needs_reauth is True


@requires_db
def test_successful_changes_clears_needs_reauth(client, admin_org, monkeypatch):
    org_id, _admin, cookies = admin_org
    connection_id = _save_notion(org_id)
    mark_needs_reauth(org_id, "notion", reason="stale")

    report = MagicMock(
        new_count=0,
        updated_count=0,
        removed_count=0,
        unchanged_count=1,
        remote_total=1,
        has_changes=False,
    )
    monkeypatch.setattr("app.api.admin.get_live_connection_token", lambda *a, **k: "tok")
    monkeypatch.setattr("app.api.admin.build_source_adapter", lambda *a, **k: MagicMock())
    monkeypatch.setattr("app.api.admin.detect_source_changes", lambda *a, **k: report)

    response = client.get(f"/admin/connections/{connection_id}/changes", cookies=cookies)
    assert response.status_code == 200
    assert list_connections(org_id)[0].needs_reauth is False


@requires_db
def test_reconnect_via_save_connection_clears_flag(store, org_cleanup):
    org_id = store.create_organization(f"Reconnect Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    _save_notion(org_id)
    mark_needs_reauth(org_id, "notion", reason="dead")
    assert list_connections(org_id)[0].needs_reauth is True

    save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_fresh",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws",
            external_workspace_name="Acme",
        ),
    )
    assert list_connections(org_id)[0].needs_reauth is False


@requires_db
def test_clear_needs_reauth_helper(store, org_cleanup):
    org_id = store.create_organization(f"Clear Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    _save_notion(org_id)
    mark_needs_reauth(org_id, "notion")
    clear_needs_reauth(org_id, "notion")
    assert list_connections(org_id)[0].needs_reauth is False


@requires_db
def test_github_health_probe_marks_needs_reauth_on_mint_failure(
    client, admin_org, monkeypatch
):
    org_id, _admin, cookies = admin_org
    connection_id = save_connection(
        org_id,
        "github",
        OAuthTokens(
            access_token="user_tok",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="inst-1",
            external_workspace_name="acme",
        ),
    )
    from app.auth.credentials import set_connection_config

    set_connection_config(
        org_id,
        "github",
        {
            "installation_id": "999",
            "account_login": "acme",
            "repository_selection": "all",
            "repos": [],
        },
    )

    def boom(_installation_id):
        from app.core.exceptions import OAuthError

        raise OAuthError("GitHub installation-token request failed: 401")

    monkeypatch.setattr("app.auth.github_app.mint_installation_token", boom)

    # Force cache miss by clearing
    from app.auth.credentials import clear_installation_token_cache

    clear_installation_token_cache(org_id)

    response = client.get(
        f"/admin/connections/{connection_id}/health", cookies=cookies
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "oauth_reauth_required"
    assert list_connections(org_id)[0].needs_reauth is True


@requires_db
def test_github_health_ok_clears_flag(client, admin_org, monkeypatch):
    org_id, _admin, cookies = admin_org
    connection_id = save_connection(
        org_id,
        "github",
        OAuthTokens(
            access_token="user_tok",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="inst-1",
            external_workspace_name="acme",
        ),
    )
    from app.auth.credentials import set_connection_config
    from app.auth.github_app import InstallationToken
    from datetime import datetime, timedelta, timezone

    set_connection_config(
        org_id,
        "github",
        {
            "installation_id": "42",
            "account_login": "acme",
            "repository_selection": "all",
            "repos": [],
        },
    )
    mark_needs_reauth(org_id, "github", reason="stale")

    def ok(_installation_id):
        return InstallationToken(
            token="ghs_ok",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    monkeypatch.setattr("app.auth.github_app.mint_installation_token", ok)
    from app.auth.credentials import clear_installation_token_cache

    clear_installation_token_cache(org_id)

    response = client.get(
        f"/admin/connections/{connection_id}/health", cookies=cookies
    )
    assert response.status_code == 200
    assert response.json()["needs_reauth"] is False
    assert list_connections(org_id)[0].needs_reauth is False
