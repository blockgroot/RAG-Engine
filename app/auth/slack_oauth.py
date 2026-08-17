"""Slack implementation of the ``OAuthProvider`` interface.

Phase 1 of the Slack Integration Plan (docs/plans/2026-08-17-slack-integration.md,
decision D6): one bot token per Slack team, installed once via the standard
"Add to Slack" v2 OAuth flow. This module only gets the bot token connected
and saved — no channel picker, no adapter, no ingestion yet (later phases).

Structurally closest to ``google_oauth.py`` (public OAuth2 endpoints over
plain ``httpx``, zero new dependencies), with one Slack-specific wrinkle:

**Slack's token endpoint returns HTTP 200 even on failure.** Errors are
signalled by ``"ok": false`` + an ``"error"`` field in an otherwise-200
response, never an HTTP error status — so ``response.raise_for_status()``
alone would silently accept a failed exchange. Every response is checked for
``ok`` explicitly before anything else is read from it.

``refresh()`` is intentionally left as the ABC's default ``NotImplementedError``:
a standard (non-token-rotation) Slack app install issues a bot token that does
not expire, the same non-expiring shape as Notion's internal-integration token.
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
                "Slack OAuth requires SLACK_CLIENT_ID, SLACK_CLIENT_SECRET, and "
                "SLACK_REDIRECT_URI to be set (create a Slack App at "
                "api.slack.com/apps to obtain these)."
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
