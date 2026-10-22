"""Phase 1 (Slack Integration Plan): SlackOAuthProvider.

No network for the OAuth exchange: ``httpx.post`` is monkeypatched, mirroring
``tests/test_google_oauth.py``'s pattern.
"""

from __future__ import annotations

import pytest

from app.auth import build_oauth_provider
from app.auth.slack_oauth import SlackOAuthProvider
from app.config.settings import SlackSettings
from app.core.exceptions import ConfigurationError, OAuthError


@pytest.fixture
def _slack_oauth_settings(monkeypatch):
    monkeypatch.setenv("SLACK_CLIENT_ID", "1234.5678")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "slack-secret-abc")
    monkeypatch.setenv("SLACK_REDIRECT_URI", "https://portal.example.com/auth/slack/callback")
    return SlackSettings.from_env()


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


def test_slack_oauth_provider_requires_client_credentials(monkeypatch):
    monkeypatch.delenv("SLACK_CLIENT_ID", raising=False)
    monkeypatch.delenv("SLACK_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SLACK_REDIRECT_URI", raising=False)
    with pytest.raises(ConfigurationError):
        SlackOAuthProvider(settings=SlackSettings.from_env())


def test_authorize_url_contains_expected_params(_slack_oauth_settings):
    provider = SlackOAuthProvider(settings=_slack_oauth_settings)
    url = provider.authorize_url("csrf-state-abc")
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "state=csrf-state-abc" in url
    assert "client_id=1234.5678" in url
    assert "channels%3Ahistory" in url  # scope is comma-delimited, URL-encoded


def test_build_oauth_provider_factory_returns_slack(_slack_oauth_settings):
    provider = build_oauth_provider("slack")
    assert isinstance(provider, SlackOAuthProvider)


def test_exchange_code_success(_slack_oauth_settings, monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["post_url"] = url
        captured["post_kwargs"] = kwargs
        return FakeResponse(
            {
                "ok": True,
                "access_token": "xoxb-bot-token-xyz",
                "token_type": "bot",
                "scope": "channels:history,channels:read",
                "bot_user_id": "U0BOT123",
                "app_id": "A0APP123",
                "team": {"id": "T0123ABC", "name": "Acme Corp"},
            }
        )

    monkeypatch.setattr("app.auth.slack_oauth.httpx.post", fake_post)

    provider = SlackOAuthProvider(settings=_slack_oauth_settings)
    tokens = provider.exchange_code("auth-code-1")

    assert tokens.access_token == "xoxb-bot-token-xyz"
    assert tokens.refresh_token is None
    assert tokens.expires_at is None
    assert tokens.external_workspace_id == "T0123ABC"
    assert tokens.external_workspace_name == "Acme Corp"

    assert captured["post_url"] == "https://slack.com/api/oauth.v2.access"
    assert captured["post_kwargs"]["data"]["client_id"] == _slack_oauth_settings.client_id


def test_exchange_code_raises_on_slack_ok_false(_slack_oauth_settings, monkeypatch):
    # Slack's failure shape is HTTP 200 with {"ok": false, "error": "..."} —
    # raise_for_status() alone would miss this.
    monkeypatch.setattr(
        "app.auth.slack_oauth.httpx.post",
        lambda url, **kwargs: FakeResponse({"ok": False, "error": "invalid_code"}),
    )

    provider = SlackOAuthProvider(settings=_slack_oauth_settings)
    with pytest.raises(OAuthError, match="invalid_code"):
        provider.exchange_code("bad-code")


def test_exchange_code_raises_on_missing_access_token(_slack_oauth_settings, monkeypatch):
    monkeypatch.setattr(
        "app.auth.slack_oauth.httpx.post",
        lambda url, **kwargs: FakeResponse({"ok": True, "team": {"id": "T1"}}),
    )

    provider = SlackOAuthProvider(settings=_slack_oauth_settings)
    with pytest.raises(OAuthError):
        provider.exchange_code("auth-code-1")


def test_exchange_code_raises_on_missing_team_id(_slack_oauth_settings, monkeypatch):
    monkeypatch.setattr(
        "app.auth.slack_oauth.httpx.post",
        lambda url, **kwargs: FakeResponse({"ok": True, "access_token": "xoxb-abc"}),
    )

    provider = SlackOAuthProvider(settings=_slack_oauth_settings)
    with pytest.raises(OAuthError):
        provider.exchange_code("auth-code-1")


def test_exchange_code_raises_oauth_error_on_http_failure(_slack_oauth_settings, monkeypatch):
    import httpx

    def fake_post(url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr("app.auth.slack_oauth.httpx.post", fake_post)

    provider = SlackOAuthProvider(settings=_slack_oauth_settings)
    with pytest.raises(OAuthError):
        provider.exchange_code("auth-code-1")


def test_refresh_not_implemented(_slack_oauth_settings):
    # Bot tokens from a standard (non-token-rotation) install don't expire —
    # same non-expiring shape as Notion's internal-integration token.
    provider = SlackOAuthProvider(settings=_slack_oauth_settings)
    with pytest.raises(NotImplementedError):
        provider.refresh("irrelevant")
