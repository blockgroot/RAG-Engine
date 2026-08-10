"""Company Sources and spaces must not share one GitHub App installation.

Covers the install-chooser redirect (no silent auto-pick) and bidirectional
uniqueness of ``installation_id`` within a Folio org.
"""

from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth import (
    OAuthTokens,
    create_admin,
    save_connection,
    set_connection_config,
)
from app.auth.github_oauth import GitHubAppProvider
from app.auth.oauth_state import create_state
from app.auth.github_pending import create_github_install_pending
from app.githublive import load_scope
from app.workspaces import create_workspace

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("EMAIL_SENDER", "console")
    monkeypatch.setenv("GITHUB_APP_SLUG", "acme-rag")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.abc123")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "unused")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


def _fake_token(monkeypatch):
    monkeypatch.setattr(
        "app.auth.credentials._github_installation_token",
        lambda org_id, workspace_id=None: "ghs_token",
    )


@requires_db
def test_callback_without_installation_id_sends_user_to_chooser(
    client, store, org_cleanup, monkeypatch
):
    """Plain user OAuth must not auto-bind — show the account picker instead."""
    org_id = store.create_organization(f"Pick {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)

    def _list(self, code):
        return (
            "ghu_user",
            None,
            None,
            [
                {"id": 1, "account": {"login": "sana", "type": "User"}},
                {"id": 2, "account": {"login": "acme-inc", "type": "Organization"}},
            ],
        )

    monkeypatch.setattr(GitHubAppProvider, "exchange_code_list_installations", _list)

    state = create_state(org_id, "github")
    response = client.get(
        f"/auth/github/callback?code=abc&state={state}",
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "/connect/github/choose?pending=" in location


@requires_db
def test_chooser_marks_company_install_unavailable_for_a_space(
    client, store, org_cleanup, monkeypatch
):
    org_id = store.create_organization(f"Unavail {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    save_connection(
        org_id,
        "github",
        OAuthTokens(
            access_token="ghu",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="acme-inc",
        ),
    )
    set_connection_config(
        org_id,
        "github",
        {"installation_id": "ORG-1", "account_login": "acme-inc"},
    )
    workspace_id = create_workspace(org_id, "Side", admin.id)

    pending = create_github_install_pending(
        org_id, workspace_id=workspace_id, access_token="ghu_pending"
    )

    monkeypatch.setattr(
        GitHubAppProvider,
        "_list_installations",
        lambda self, token: [
            {"id": "ORG-1", "account": {"login": "acme-inc", "type": "Organization"}},
            {"id": "USER-9", "account": {"login": "sana", "type": "User"}},
        ],
    )

    detail = client.get(f"/auth/github/installations/pending/{pending}").json()
    by_id = {i["id"]: i for i in detail["installations"]}
    assert by_id["ORG-1"]["available"] is False
    assert "Company" in (by_id["ORG-1"]["unavailable_reason"] or "")
    assert by_id["USER-9"]["available"] is True
    assert detail["scope"] == "workspace"
    assert "github.com/apps/acme-rag/installations/new" in detail["install_another_url"]
    assert "github.com/logout" in detail["switch_account_url"]


@requires_db
def test_chooser_completes_a_distinct_personal_install(
    client, store, org_cleanup, monkeypatch
):
    org_id = store.create_organization(f"Choose {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    save_connection(
        org_id,
        "github",
        OAuthTokens(
            access_token="ghu",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="acme-inc",
        ),
    )
    set_connection_config(
        org_id,
        "github",
        {"installation_id": "ORG-1", "account_login": "acme-inc"},
    )
    workspace_id = create_workspace(org_id, "Side", admin.id)
    pending = create_github_install_pending(
        org_id, workspace_id=workspace_id, access_token="ghu_pending"
    )
    _fake_token(monkeypatch)

    def _tokens(self, access_token, installation_id, **kw):
        return (
            OAuthTokens(
                access_token=access_token,
                refresh_token=None,
                expires_at=None,
                external_workspace_id="sana",
                external_workspace_name="sana (User)",
            ),
            str(installation_id),
        )

    monkeypatch.setattr(GitHubAppProvider, "tokens_for_installation", _tokens)

    class _Repo:
        def __init__(self, full_name):
            self.full_name = full_name
            self.description = None
            self.topics = ()

    monkeypatch.setattr(
        "app.githublive.scope.fetch_installation_repos",
        lambda token, **kw: ("selected", [_Repo("sana/notes")]),
    )

    response = client.post(
        f"/auth/github/installations/pending/{pending}",
        json={"installation_id": "USER-9"},
    )
    assert response.status_code == 200
    assert f"/workspaces/{workspace_id}?connected=github" in response.json()["redirect_to"]
    assert load_scope(org_id, workspace_id).installation_id == "USER-9"
    assert load_scope(org_id).installation_id == "ORG-1"


@requires_db
def test_org_connect_cannot_reuse_a_space_installation(
    client, store, org_cleanup, monkeypatch
):
    """Bidirectional uniqueness — personal-on-both was the reported mix-up."""
    org_id = store.create_organization(f"Bi {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Side", admin.id)
    save_connection(
        org_id,
        "github",
        OAuthTokens(
            access_token="ghu",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="sana",
        ),
        workspace_id=workspace_id,
    )
    set_connection_config(
        org_id,
        "github",
        {"installation_id": "USER-1", "account_login": "sana"},
        workspace_id=workspace_id,
    )

    def _exchange(self, code, installation_id):
        return (
            OAuthTokens(
                access_token="ghu_admin",
                refresh_token=None,
                expires_at=None,
                external_workspace_id="sana",
                external_workspace_name="sana (User)",
            ),
            installation_id,
        )

    monkeypatch.setattr(GitHubAppProvider, "exchange_code_with_installation", _exchange)
    _fake_token(monkeypatch)

    state = create_state(org_id, "github")  # org-wide
    response = client.get(
        f"/auth/github/callback?code=abc&state={state}&installation_id=USER-1",
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "connect_error=github_install_in_use" in location
    assert "/admin/connections" in location

    from app.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError):
        load_scope(org_id)
