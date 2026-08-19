"""LinearOAuthProvider: OAuth "Connect Linear" flow.

No network: ``httpx.post`` is monkeypatched, mirroring
``tests/test_slack_oauth.py``/``tests/test_google_oauth.py``'s pattern. Two
calls happen per successful exchange — the token exchange, then a GraphQL
identity lookup (Linear's token response carries no workspace id, same
reasoning as Google's Drive `about` call).
"""

from __future__ import annotations

import pytest

from app.auth import build_oauth_provider
from app.auth.linear_oauth import LinearOAuthProvider
from app.config.settings import LinearSettings
from app.core.exceptions import ConfigurationError, OAuthError


@pytest.fixture
def _linear_oauth_settings(monkeypatch):
    monkeypatch.setenv("LINEAR_CLIENT_ID", "client-abc")
    monkeypatch.setenv("LINEAR_CLIENT_SECRET", "secret-xyz")
    monkeypatch.setenv("LINEAR_REDIRECT_URI", "https://portal.example.com/auth/linear/callback")
    return LinearSettings.from_env()


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


def _fake_post_sequence(*payloads):
    calls = list(payloads)

    def fake_post(url, **kwargs):
        return FakeResponse(calls.pop(0))

    return fake_post


def test_linear_oauth_provider_requires_client_credentials(monkeypatch):
    monkeypatch.delenv("LINEAR_CLIENT_ID", raising=False)
    monkeypatch.delenv("LINEAR_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("LINEAR_REDIRECT_URI", raising=False)
    with pytest.raises(ConfigurationError):
        LinearOAuthProvider(settings=LinearSettings.from_env())


def test_authorize_url_contains_expected_params(_linear_oauth_settings):
    provider = LinearOAuthProvider(settings=_linear_oauth_settings)
    url = provider.authorize_url("csrf-state-abc")
    assert url.startswith("https://linear.app/oauth/authorize?")
    assert "state=csrf-state-abc" in url
    assert "client_id=client-abc" in url
    assert "scope=read" in url


def test_build_oauth_provider_factory_returns_linear(_linear_oauth_settings):
    provider = build_oauth_provider("linear")
    assert isinstance(provider, LinearOAuthProvider)


def test_exchange_code_success(_linear_oauth_settings, monkeypatch):
    monkeypatch.setattr(
        "app.auth.linear_oauth.httpx.post",
        _fake_post_sequence(
            {"access_token": "lin_oauth_abc", "token_type": "Bearer", "scope": "read"},
            {"data": {"organization": {"id": "org-123", "name": "Acme Corp"}}},
        ),
    )

    provider = LinearOAuthProvider(settings=_linear_oauth_settings)
    tokens = provider.exchange_code("auth-code-1")

    assert tokens.access_token == "lin_oauth_abc"
    assert tokens.refresh_token is None
    assert tokens.external_workspace_id == "org-123"
    assert tokens.external_workspace_name == "Acme Corp"


def test_exchange_code_raises_on_missing_access_token(_linear_oauth_settings, monkeypatch):
    monkeypatch.setattr(
        "app.auth.linear_oauth.httpx.post", lambda url, **kwargs: FakeResponse({})
    )
    provider = LinearOAuthProvider(settings=_linear_oauth_settings)
    with pytest.raises(OAuthError):
        provider.exchange_code("auth-code-1")


def test_exchange_code_raises_on_missing_organization_id(_linear_oauth_settings, monkeypatch):
    monkeypatch.setattr(
        "app.auth.linear_oauth.httpx.post",
        _fake_post_sequence(
            {"access_token": "lin_oauth_abc"},
            {"data": {"organization": {}}},
        ),
    )
    provider = LinearOAuthProvider(settings=_linear_oauth_settings)
    with pytest.raises(OAuthError):
        provider.exchange_code("auth-code-1")


def test_exchange_code_raises_on_graphql_errors(_linear_oauth_settings, monkeypatch):
    monkeypatch.setattr(
        "app.auth.linear_oauth.httpx.post",
        _fake_post_sequence(
            {"access_token": "lin_oauth_abc"},
            {"errors": [{"message": "bad token"}]},
        ),
    )
    provider = LinearOAuthProvider(settings=_linear_oauth_settings)
    with pytest.raises(OAuthError):
        provider.exchange_code("auth-code-1")


def test_exchange_code_raises_oauth_error_on_http_failure(_linear_oauth_settings, monkeypatch):
    import httpx

    def fake_post(url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.auth.linear_oauth.httpx.post", fake_post)
    provider = LinearOAuthProvider(settings=_linear_oauth_settings)
    with pytest.raises(OAuthError):
        provider.exchange_code("auth-code-1")


def test_refresh_not_implemented(_linear_oauth_settings):
    # A standard Linear OAuth app issues a non-expiring access token with no
    # refresh token — same non-expiring shape as Notion/Slack.
    provider = LinearOAuthProvider(settings=_linear_oauth_settings)
    with pytest.raises(NotImplementedError):
        provider.refresh("irrelevant")
