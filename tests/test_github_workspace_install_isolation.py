"""A workspace's GitHub must be the employee's OWN installation, not the org's.

**The bug this closes.** The intended flow is: an employee creates a workspace,
invites colleagues, connects *their personal* GitHub account, and the workspace
answers only about the repos they shared. What actually happened is that a
workspace could end up bound to the **company organization's** installation —
so the workspace saw every company repo. Two independent causes:

1. When GitHub's install redirect carried an ``installation_id``, the callback
   accepted it via ``exchange_code_with_installation``, which verifies the id
   belongs to the authorizing user but says nothing about *whose* account it is.
   ``prefer_user_account`` was only consulted on the other branch.
2. ``_pick_installation`` fell back to ``installations[0]`` when no installation
   of the preferred type existed, so a workspace connect with only an
   Organization installation silently bound that one.

Both end the same way: the workspace row points at the *same installation id* as
the org-wide row — identical repos behind two connections. That is the "repos
getting mixed" symptom, and it is what these tests forbid.

The invariant: **a workspace connection may never bind the installation that
this org's org-wide connection already uses.**
"""

from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth import (
    OAuthTokens,
    create_admin,
    create_session_token,
    save_connection,
    set_connection_config,
)
from app.auth.github_oauth import GitHubAppProvider
from app.auth.oauth_state import create_state
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
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "unused-minting-is-faked")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


def _org_with_company_github(store, org_cleanup):
    """An org whose ADMIN already connected the company GitHub organization."""
    org_id = store.create_organization(f"GH Mix {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    save_connection(
        org_id,
        "github",
        OAuthTokens(
            access_token="ghu_admin",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="acme-inc",
        ),
    )
    set_connection_config(
        org_id,
        "github",
        {
            "installation_id": "ORG-INSTALL-1",
            "account_login": "acme-inc",
            "repository_selection": "all",
            "repos": [
                {"full_name": "acme-inc/payroll", "description": None, "topics": []}
            ],
        },
    )
    return org_id, admin


def _fake_token(monkeypatch):
    monkeypatch.setattr(
        "app.auth.credentials._github_installation_token",
        lambda org_id, workspace_id=None: "ghs_token",
    )


# -- cause 1: the install-redirect path --------------------------------------


@requires_db
def test_a_workspace_cannot_bind_the_company_org_installation_via_install_redirect(
    client, store, org_cleanup, monkeypatch
):
    """The employee's workspace must not inherit the company installation.

    Simulates the realistic accident: the App is already installed on the company
    org, so GitHub's redirect hands back the ORG installation id while the
    employee is connecting inside their personal workspace.
    """
    org_id, admin = _org_with_company_github(store, org_cleanup)
    workspace_id = create_workspace(org_id, "Side Project", admin.id)

    def _exchange(self, code, installation_id):
        return (
            OAuthTokens(
                access_token="ghu_employee",
                refresh_token=None,
                expires_at=None,
                external_workspace_id="acme-inc",
                external_workspace_name="acme-inc (Organization)",
            ),
            installation_id,
        )

    monkeypatch.setattr(GitHubAppProvider, "exchange_code_with_installation", _exchange)
    _fake_token(monkeypatch)

    state = create_state(org_id, "github", workspace_id=workspace_id)
    response = client.get(
        f"/auth/github/callback?code=abc&state={state}&installation_id=ORG-INSTALL-1",
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert f"/workspaces/{workspace_id}" in location
    assert "connect_error=github_same_install" in location

    # And crucially: no workspace connection was created.
    from app.core.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError):
        load_scope(org_id, workspace_id)


@requires_db
def test_a_workspace_may_bind_a_different_personal_installation(
    client, store, org_cleanup, monkeypatch
):
    """The intended flow still works: personal account, personal repos."""
    org_id, admin = _org_with_company_github(store, org_cleanup)
    workspace_id = create_workspace(org_id, "Side Project", admin.id)

    def _exchange(self, code, installation_id):
        return (
            OAuthTokens(
                access_token="ghu_employee",
                refresh_token=None,
                expires_at=None,
                external_workspace_id="sana",
                external_workspace_name="sana (User)",
            ),
            installation_id,
        )

    monkeypatch.setattr(GitHubAppProvider, "exchange_code_with_installation", _exchange)
    _fake_token(monkeypatch)

    class _Repo:
        def __init__(self, full_name):
            self.full_name = full_name
            self.description = None
            self.topics = ()

    monkeypatch.setattr(
        "app.githublive.scope.fetch_installation_repos",
        lambda token, **kw: ("selected", [_Repo("sana/my-side-project")]),
    )

    state = create_state(org_id, "github", workspace_id=workspace_id)
    response = client.get(
        f"/auth/github/callback?code=abc&state={state}&installation_id=USER-INSTALL-9",
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    ws_scope = load_scope(org_id, workspace_id)
    assert ws_scope.installation_id == "USER-INSTALL-9"
    assert ws_scope.account_login == "sana"
    assert {r.full_name for r in ws_scope.repos} == {"sana/my-side-project"}
    # The company installation is untouched and still separate.
    assert load_scope(org_id).installation_id == "ORG-INSTALL-1"


# -- cause 2: the silent fallback in installation picking --------------------


def test_a_workspace_connect_never_falls_back_to_an_organization_installation():
    """No personal installation -> return None (send them to install), not the org's.

    Falling back is how a "personal space" silently became a window onto the
    company org.
    """
    provider = GitHubAppProvider.__new__(GitHubAppProvider)
    installations = [
        {"id": 1, "account": {"login": "acme-inc", "type": "Organization"}},
    ]

    chosen = provider._pick_installation(installations, prefer_user_account=True)

    assert chosen is None


def test_a_workspace_connect_picks_the_user_installation_when_one_exists():
    provider = GitHubAppProvider.__new__(GitHubAppProvider)
    installations = [
        {"id": 1, "account": {"login": "acme-inc", "type": "Organization"}},
        {"id": 2, "account": {"login": "sana", "type": "User"}},
    ]

    chosen = provider._pick_installation(installations, prefer_user_account=True)

    assert chosen["id"] == 2


def test_an_org_connect_still_prefers_the_organization_but_may_fall_back():
    """Unchanged for the admin flow: a solo founder may only have a User install."""
    provider = GitHubAppProvider.__new__(GitHubAppProvider)

    org_first = provider._pick_installation(
        [
            {"id": 1, "account": {"login": "sana", "type": "User"}},
            {"id": 2, "account": {"login": "acme-inc", "type": "Organization"}},
        ],
        prefer_user_account=False,
    )
    only_user = provider._pick_installation(
        [{"id": 3, "account": {"login": "sana", "type": "User"}}],
        prefer_user_account=False,
    )

    assert org_first["id"] == 2
    assert only_user["id"] == 3
