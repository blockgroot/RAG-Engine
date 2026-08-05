"""The GitHub connect callback, end to end through the API (Plan Phase 4).

Proves the two things that make the GitHub connection trustworthy:

- The verified installation id AND the admin's real authorized repo scope are
  both persisted, so nothing downstream has to assume "connected" means "every
  repo" (decision D5b).
- A failure while reading that scope does **not** abort an otherwise-successful
  connect, and leaves the connection failing *closed* rather than open.
"""

from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api.main import app
from app.auth import create_admin
from app.auth.base import OAuthTokens
from app.auth.oauth_state import create_state
from app.core.exceptions import SourceError
from app.githublive import load_scope

from .conftest import requires_db


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _github_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("GITHUB_APP_SLUG", "acme-rag")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.abc123")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "unused-because-minting-is-faked")


def _fake_exchange(monkeypatch, *, account_login="acme-inc"):
    from app.auth.github_oauth import GitHubAppProvider

    def _exchange(self, code, installation_id):
        return (
            OAuthTokens(
                access_token="ghu_user_token",
                refresh_token=None,
                expires_at=None,
                external_workspace_id=account_login,
                external_workspace_name=f"{account_login} (Organization)",
            ),
            installation_id,
        )

    monkeypatch.setattr(
        GitHubAppProvider, "exchange_code_with_installation", _exchange
    )


def _fake_token(monkeypatch):
    """Skip real JWT minting — Phase 1/3 already cover that path."""
    monkeypatch.setattr(
        "app.auth.credentials._github_installation_token",
        lambda org_id, workspace_id=None: "ghs_installation_token",
    )


@requires_db
def test_callback_persists_verified_installation_and_authorized_scope(
    client, store, org_cleanup, monkeypatch
):
    org_id = store.create_organization("GH Connect Scope Org")
    org_cleanup.append(org_id)
    create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)

    _fake_exchange(monkeypatch)
    _fake_token(monkeypatch)
    monkeypatch.setattr(
        "app.githublive.scope.fetch_installation_repos",
        lambda token, **kw: (
            "selected",
            [
                _RepoRef("acme-inc/handbook", "Engineering handbook", ("docs",)),
                _RepoRef("acme-inc/payments-svc", "Billing", ("go",)),
            ],
        ),
    )

    state = create_state(org_id, "github")
    response = client.get(
        f"/auth/github/callback?code=abc&state={state}&installation_id=4242"
        "&setup_action=install",
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)

    scope = load_scope(org_id)
    assert scope.installation_id == "4242"
    assert scope.account_login == "acme-inc"
    # The admin picked a subset; that must be recorded as such, not widened.
    assert scope.repository_selection == "selected"
    assert {r.full_name for r in scope.repos} == {
        "acme-inc/handbook",
        "acme-inc/payments-svc",
    }
    # Descriptions are what let the model resolve a repo without embeddings.
    assert scope.repos[0].description == "Engineering handbook"


@requires_db
def test_callback_survives_a_scope_read_failure_and_fails_closed(
    client, store, org_cleanup, monkeypatch
):
    org_id = store.create_organization("GH Connect Scope Failure Org")
    org_cleanup.append(org_id)
    create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)

    _fake_exchange(monkeypatch)
    _fake_token(monkeypatch)

    def _boom(token, **kw):
        raise SourceError("GitHub is having a bad day")

    monkeypatch.setattr("app.githublive.scope.fetch_installation_repos", _boom)

    state = create_state(org_id, "github")
    response = client.get(
        f"/auth/github/callback?code=abc&state={state}&installation_id=4242",
        follow_redirects=False,
    )

    # The connection itself succeeded ...
    assert response.status_code in (302, 307)
    scope = load_scope(org_id)
    assert scope.installation_id == "4242"
    # ... but with no authorized repos recorded, so every lookup is refused
    # until the scope is refreshed. Fail closed, never open.
    assert scope.repos == ()
    from app.githublive import resolve_repo

    with pytest.raises(SourceError):
        resolve_repo(scope, "acme-inc/handbook")


@requires_db
def test_callback_rejects_github_without_an_installation_id(
    client, store, org_cleanup, monkeypatch
):
    """A GitHub callback with no installation id cannot produce a usable connection."""
    org_id = store.create_organization("GH Connect No Install Org")
    org_cleanup.append(org_id)

    state = create_state(org_id, "github")
    response = client.get(
        f"/auth/github/callback?code=abc&state={state}", follow_redirects=False
    )

    assert response.status_code == 400


class _RepoRef:
    """Duck-typed stand-in for githublive.RepoRef (kept local to this module)."""

    def __init__(self, full_name, description=None, topics=()):
        self.full_name = full_name
        self.description = description
        self.topics = tuple(topics)
