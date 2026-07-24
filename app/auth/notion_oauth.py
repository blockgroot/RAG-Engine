"""Notion implementation of the ``OAuthProvider`` interface.

Uses Notion's public OAuth2 endpoints directly via ``httpx`` (already a
transitive dependency of ``notion-client``, now used directly here — same
dependency-light reasoning as the rest of ``app/``). Notion's authorization-code
exchange returns an access token that does **not expire** and has **no refresh
token** (per Notion's own docs), so ``refresh()`` is intentionally left
unimplemented (the ABC's default ``NotImplementedError``).

Reuses the already-scaffolded ``NotionSettings.client_id/client_secret/redirect_uri``
(read since Phase 4, unused until now) — no new env vars needed for Notion.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from ..config.settings import NotionSettings
from ..core.exceptions import ConfigurationError, OAuthError
from .base import OAuthProvider, OAuthTokens

_AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
_TIMEOUT = 15.0


class NotionOAuthProvider(OAuthProvider):
    """Drives Notion's "Connect" OAuth flow for a public Notion integration."""

    def __init__(self, settings: NotionSettings | None = None) -> None:
        settings = settings or NotionSettings.from_env()
        if not (settings.client_id and settings.client_secret and settings.redirect_uri):
            raise ConfigurationError(
                "Notion OAuth requires NOTION_CLIENT_ID, NOTION_CLIENT_SECRET, and "
                "NOTION_REDIRECT_URI to be set (create a public Notion integration to "
                "obtain these)."
            )
        self._client_id = settings.client_id
        self._client_secret = settings.client_secret
        self._redirect_uri = settings.redirect_uri

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self._client_id,
            "response_type": "code",
            "owner": "user",
            "redirect_uri": self._redirect_uri,
            "state": state,
        }
        return f"{_AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> OAuthTokens:
        try:
            response = httpx.post(
                _TOKEN_URL,
                auth=(self._client_id, self._client_secret),
                json={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._redirect_uri,
                },
                headers={"Content-Type": "application/json"},
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(f"Notion OAuth code exchange failed: {exc}", cause=exc) from exc

        data = response.json()
        access_token = data.get("access_token")
        workspace_id = data.get("workspace_id")
        if not access_token or not workspace_id:
            raise OAuthError(
                "Notion OAuth response missing access_token or workspace_id"
            )

        return OAuthTokens(
            access_token=access_token,
            refresh_token=None,
            expires_at=None,
            external_workspace_id=workspace_id,
            external_workspace_name=data.get("workspace_name"),
        )
