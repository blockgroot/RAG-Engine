"""Phase 3 (Google Integration Plan): GoogleOAuthProvider.

No network for the OAuth exchange/refresh/identity calls: ``httpx.post`` and
``httpx.get`` are monkeypatched, mirroring ``tests/test_auth.py``'s pattern for
Notion.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import build_oauth_provider
from app.auth.google_oauth import GoogleOAuthProvider
from app.config.settings import GoogleSettings
from app.core.exceptions import ConfigurationError, OAuthError


@pytest.fixture
def _google_oauth_settings(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://portal.example.com/auth/google/callback")
    return GoogleSettings.from_env()


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("POST", "https://example.com")
            response = httpx.Response(self.status_code, request=request, json=self._payload)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._payload


REQUESTED_SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/documents.readonly"
)


def test_google_oauth_provider_requires_client_credentials(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    with pytest.raises(ConfigurationError):
        GoogleOAuthProvider(settings=GoogleSettings.from_env())


def test_authorize_url_contains_expected_params(_google_oauth_settings):
    provider = GoogleOAuthProvider(settings=_google_oauth_settings)
    url = provider.authorize_url("csrf-state-abc")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=csrf-state-abc" in url
    assert "drive.readonly" in url
    assert "documents.readonly" in url


def test_build_oauth_provider_factory_returns_google(_google_oauth_settings):
    provider = build_oauth_provider("google")
    assert isinstance(provider, GoogleOAuthProvider)


def test_exchange_code_success(_google_oauth_settings, monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["post_url"] = url
        captured["post_kwargs"] = kwargs
        return FakeResponse(
            {
                "access_token": "ya29.access-xyz",
                "refresh_token": "1//refresh-xyz",
                "expires_in": 3599,
                "scope": REQUESTED_SCOPES,
                "token_type": "Bearer",
            }
        )

    def fake_get(url, **kwargs):
        captured["get_url"] = url
        captured["get_kwargs"] = kwargs
        return FakeResponse(
            {
                "user": {
                    "emailAddress": "admin@acme.com",
                    "displayName": "Acme Admin",
                    "permissionId": "12345",
                }
            }
        )

    monkeypatch.setattr("app.auth.google_oauth.httpx.post", fake_post)
    monkeypatch.setattr("app.auth.google_oauth.httpx.get", fake_get)

    provider = GoogleOAuthProvider(settings=_google_oauth_settings)
    before = datetime.now(timezone.utc)
    tokens = provider.exchange_code("auth-code-1")
    after = datetime.now(timezone.utc)

    assert tokens.access_token == "ya29.access-xyz"
    assert tokens.refresh_token == "1//refresh-xyz"
    assert tokens.external_workspace_id == "admin@acme.com"
    assert tokens.external_workspace_name == "Acme Admin"
    assert tokens.expires_at is not None
    assert before + timedelta(seconds=3599) <= tokens.expires_at <= after + timedelta(seconds=3599)

    assert captured["post_url"] == "https://oauth2.googleapis.com/token"
    assert "data" in captured["post_kwargs"]  # form-encoded, not JSON
    assert captured["post_kwargs"]["data"]["client_id"] == _google_oauth_settings.client_id
    assert captured["get_url"] == "https://www.googleapis.com/drive/v3/about"


def test_exchange_code_raises_on_insufficient_scope(_google_oauth_settings, monkeypatch):
    def fake_post(url, **kwargs):
        return FakeResponse(
            {
                "access_token": "ya29.access-xyz",
                "expires_in": 3599,
                "scope": "https://www.googleapis.com/auth/drive.readonly",  # missing documents scope
            }
        )

    monkeypatch.setattr("app.auth.google_oauth.httpx.post", fake_post)

    provider = GoogleOAuthProvider(settings=_google_oauth_settings)
    with pytest.raises(OAuthError, match="insufficient"):
        provider.exchange_code("auth-code-1")


def test_exchange_code_raises_on_missing_access_token(_google_oauth_settings, monkeypatch):
    monkeypatch.setattr(
        "app.auth.google_oauth.httpx.post", lambda url, **kwargs: FakeResponse({})
    )

    provider = GoogleOAuthProvider(settings=_google_oauth_settings)
    with pytest.raises(OAuthError):
        provider.exchange_code("auth-code-1")


def test_exchange_code_raises_oauth_error_on_http_failure(_google_oauth_settings, monkeypatch):
    import httpx

    def fake_post(url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.auth.google_oauth.httpx.post", fake_post)

    provider = GoogleOAuthProvider(settings=_google_oauth_settings)
    with pytest.raises(OAuthError):
        provider.exchange_code("auth-code-1")


def test_refresh_success_returns_updated_tokens(_google_oauth_settings, monkeypatch):
    def fake_post(url, **kwargs):
        assert kwargs["data"]["grant_type"] == "refresh_token"
        assert kwargs["data"]["refresh_token"] == "1//refresh-xyz"
        return FakeResponse({"access_token": "ya29.new-access", "expires_in": 3600})

    monkeypatch.setattr("app.auth.google_oauth.httpx.post", fake_post)

    provider = GoogleOAuthProvider(settings=_google_oauth_settings)
    tokens = provider.refresh("1//refresh-xyz")

    assert tokens.access_token == "ya29.new-access"
    assert tokens.refresh_token is None
    assert tokens.expires_at is not None


def test_refresh_raises_on_invalid_grant(_google_oauth_settings, monkeypatch):
    import httpx

    def fake_post(url, **kwargs):
        request = httpx.Request("POST", url)
        response = httpx.Response(
            400, request=request, json={"error": "invalid_grant", "error_description": "Token expired"}
        )
        raise httpx.HTTPStatusError("400", request=request, response=response)

    monkeypatch.setattr("app.auth.google_oauth.httpx.post", fake_post)

    provider = GoogleOAuthProvider(settings=_google_oauth_settings)
    with pytest.raises(OAuthError, match="invalid_grant"):
        provider.refresh("1//dead-refresh-token")
