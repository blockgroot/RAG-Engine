"""Slack ``OAuthProvider``: one bot token per Slack team via "Add to Slack" v2.

Slack's token endpoint returns HTTP 200 on failure, so every response's
``ok`` is checked explicitly. A standard bot token does not expire, so
``refresh()`` stays ``NotImplementedError``.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from ..config.settings import SlackSettings
from ..core.exceptions import ConfigurationError, OAuthError
from .base import OAuthProvider, OAuthTokens

_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
_TOKEN_URL = "https://slack.com/api/oauth.v2.access"
_TIMEOUT = 15.0


class SlackOAuthProvider(OAuthProvider):
    """Drives Slack's "Add to Slack" bot-token OAuth flow."""

    def __init__(self, settings: SlackSettings | None = None) -> None:
        settings = settings or SlackSettings.from_env()
        if not (settings.client_id and settings.client_secret and settings.redirect_uri):
            raise ConfigurationError(
                "Your company's Slack app isn't set up yet. An admin adds it under "
                "Sources → Slack → Set up your Slack app (self-hosted: "
                "SLACK_CLIENT_ID, SLACK_CLIENT_SECRET and SLACK_REDIRECT_URI in .env)."
            )
        self._settings = settings

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "scope": self._settings.scopes,
            "state": state,
        }
        return f"{_AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> OAuthTokens:
        data = self._access(code)
        access_token = data.get("access_token")
        if not access_token:
            raise OAuthError("Slack OAuth response missing access_token")

        team = data.get("team") or {}
        team_id = team.get("id")
        if not team_id:
            raise OAuthError("Slack OAuth response missing team.id")

        return OAuthTokens(
            access_token=access_token,
            refresh_token=None,
            expires_at=None,
            external_workspace_id=team_id,
            external_workspace_name=team.get("name"),
        )

    def _access(self, code: str) -> dict:
        try:
            response = httpx.post(
                _TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._settings.client_id,
                    "client_secret": self._settings.client_secret,
                    "redirect_uri": self._settings.redirect_uri,
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(f"Slack OAuth code exchange failed: {exc}", cause=exc) from exc

        data = response.json()
        if not data.get("ok"):
            # Slack's failure shape: HTTP 200 with {"ok": false, "error": "..."}.
            raise OAuthError(f"Slack OAuth code exchange failed: {data.get('error')}")
        return data
