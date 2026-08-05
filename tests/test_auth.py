"""Phase 11: OAuth provider abstraction + DB-backed connection credentials.

No network for the OAuth exchange: ``httpx.post`` is monkeypatched so
``NotionOAuthProvider`` is tested deterministically. Credential-storage tests
that touch the DB use the real Postgres+pgvector store (skipped automatically
without DATABASE_URL, same convention as the rest of the suite).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.auth import build_oauth_provider, get_connection_token, list_connections, save_connection
from app.auth.base import OAuthTokens
from app.auth.notion_oauth import NotionOAuthProvider
from app.config.settings import AuthSettings, NotionSettings
from app.core.exceptions import ConfigurationError, OAuthError

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_encryption_key(monkeypatch):
    """A real Fernet key so encrypt/decrypt work end to end in these tests."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


@pytest.fixture
def _notion_oauth_settings(monkeypatch):
    monkeypatch.setenv("NOTION_CLIENT_ID", "client-123")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("NOTION_REDIRECT_URI", "https://portal.example.com/auth/notion/callback")
    return NotionSettings.from_env()


def test_notion_oauth_provider_requires_client_credentials(monkeypatch):
    monkeypatch.delenv("NOTION_CLIENT_ID", raising=False)
    monkeypatch.delenv("NOTION_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("NOTION_REDIRECT_URI", raising=False)
    with pytest.raises(ConfigurationError):
        NotionOAuthProvider(settings=NotionSettings.from_env())


def test_authorize_url_includes_state_and_redirect(_notion_oauth_settings):
    provider = NotionOAuthProvider(settings=_notion_oauth_settings)
    url = provider.authorize_url("csrf-state-abc")
    assert url.startswith("https://api.notion.com/v1/oauth/authorize?")
    assert "state=csrf-state-abc" in url
    assert "client_id=client-123" in url


def test_build_oauth_provider_factory_returns_notion(_notion_oauth_settings):
    provider = build_oauth_provider("notion")
    assert isinstance(provider, NotionOAuthProvider)


def test_build_oauth_provider_rejects_unknown_provider():
    # This test has now been re-pointed twice, each time a provider graduated
    # from "unimplemented" to real: first "google" (Google Integration Plan
    # Phase 3), now "github" (GitHub Integration Plan Phase 2). "slack" is the
    # current genuinely-unknown name; move this again if Slack ever lands.
    with pytest.raises(ConfigurationError):
        build_oauth_provider("slack")


def test_exchange_code_success(_notion_oauth_settings, monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "access_token": "ntn_access_xyz",
                "workspace_id": "ws-1",
                "workspace_name": "Acme Corp",
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeResponse()

    monkeypatch.setattr("app.auth.notion_oauth.httpx.post", fake_post)

    provider = NotionOAuthProvider(settings=_notion_oauth_settings)
    tokens = provider.exchange_code("auth-code-1")

    assert tokens.access_token == "ntn_access_xyz"
    assert tokens.external_workspace_id == "ws-1"
    assert tokens.external_workspace_name == "Acme Corp"
    assert tokens.refresh_token is None
    assert captured["url"] == "https://api.notion.com/v1/oauth/token"


def test_exchange_code_raises_oauth_error_on_http_failure(_notion_oauth_settings, monkeypatch):
    import httpx

    def fake_post(url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.auth.notion_oauth.httpx.post", fake_post)

    provider = NotionOAuthProvider(settings=_notion_oauth_settings)
    with pytest.raises(OAuthError):
        provider.exchange_code("auth-code-1")


def test_exchange_code_raises_oauth_error_on_missing_fields(_notion_oauth_settings, monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {}  # no access_token / workspace_id

    monkeypatch.setattr(
        "app.auth.notion_oauth.httpx.post", lambda url, **kwargs: FakeResponse()
    )

    provider = NotionOAuthProvider(settings=_notion_oauth_settings)
    with pytest.raises(OAuthError):
        provider.exchange_code("auth-code-1")


def test_refresh_not_supported_for_notion(_notion_oauth_settings):
    provider = NotionOAuthProvider(settings=_notion_oauth_settings)
    with pytest.raises(NotImplementedError):
        provider.refresh("some-refresh-token")


# -- DB-backed credential storage (requires a real Postgres) -------------------


@requires_db
def test_save_and_get_connection_token_round_trip(store, org_cleanup):
    org_id = store.create_organization("Auth Test Org A")
    org_cleanup.append(org_id)

    tokens = OAuthTokens(
        access_token="ntn_org_a_secret",
        refresh_token=None,
        expires_at=None,
        external_workspace_id="ws-a",
        external_workspace_name="Org A Workspace",
    )
    save_connection(org_id, "notion", tokens)

    assert get_connection_token(org_id, "notion") == "ntn_org_a_secret"

    infos = list_connections(org_id)
    assert len(infos) == 1
    assert infos[0].provider == "notion"
    assert infos[0].external_workspace_name == "Org A Workspace"


@requires_db
def test_connection_lookup_never_crosses_orgs(store, org_cleanup):
    org_a = store.create_organization("Auth Isolation Org A")
    org_b = store.create_organization("Auth Isolation Org B")
    org_cleanup.extend([org_a, org_b])

    save_connection(
        org_a,
        "notion",
        OAuthTokens(
            access_token="ntn_a_secret",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-a",
        ),
    )
    save_connection(
        org_b,
        "notion",
        OAuthTokens(
            access_token="ntn_b_secret",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-b",
        ),
    )

    assert get_connection_token(org_a, "notion") == "ntn_a_secret"
    assert get_connection_token(org_b, "notion") == "ntn_b_secret"
    # org_a's list must never contain org_b's connection or vice versa.
    assert {c.external_workspace_id for c in list_connections(org_a)} == {"ws-a"}
    assert {c.external_workspace_id for c in list_connections(org_b)} == {"ws-b"}


@requires_db
def test_get_connection_token_raises_when_not_connected(store, org_cleanup):
    org_id = store.create_organization("Auth No Connection Org")
    org_cleanup.append(org_id)
    with pytest.raises(ConfigurationError):
        get_connection_token(org_id, "notion")


@requires_db
def test_reconnect_replaces_existing_connection(store, org_cleanup):
    org_id = store.create_organization("Auth Reconnect Org")
    org_cleanup.append(org_id)

    save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_old",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-old",
        ),
    )
    save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_new",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-new",
        ),
    )

    assert get_connection_token(org_id, "notion") == "ntn_new"
    assert len(list_connections(org_id)) == 1  # replaced, not duplicated
