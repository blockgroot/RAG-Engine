"""Linear ``OAuthProvider``.

The token response names no workspace, so a GraphQL ``organization`` query
resolves it. Tokens expire (~1 day) and the refresh token rotates on every
use, so ``refresh()`` returns the new pair. OAuth tokens go to
``LinearAdapter`` with ``oauth=True``: Linear formats the header differently
from the ``LINEAR_TOKEN_<NAME>`` API-key path.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from ..config.settings import LinearSettings
from ..core.exceptions import ConfigurationError, OAuthError, OAuthReauthRequiredError
from .base import CLIENT_ERRORS, OAuthProvider, OAuthTokens, compute_expires_at, oauth_error_code

_AUTHORIZE_URL = "https://linear.app/oauth/authorize"
_TOKEN_URL = "https://api.linear.app/oauth/token"
_GRAPHQL_URL = "https://api.linear.app/graphql"
_TIMEOUT = 15.0


class LinearOAuthProvider(OAuthProvider):
    """Drives Linear's "Connect" OAuth2 authorization-code flow."""

    def __init__(self, settings: LinearSettings | None = None) -> None:
        settings = settings or LinearSettings.from_env()
        if not (settings.client_id and settings.client_secret and settings.redirect_uri):
            raise ConfigurationError(
                "Linear OAuth requires LINEAR_CLIENT_ID, LINEAR_CLIENT_SECRET, and "
                "LINEAR_REDIRECT_URI to be set (create a Linear OAuth application "
                "at linear.app/settings/api/applications to obtain these)."
            )
        self._settings = settings

    def authorize_url(self, state: str, *, scopes: str | None = None) -> str:
        """``scopes`` overrides the read-only default for a person connecting
        Linear for Act mode; the org's read connection never passes it."""
        params = {
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "response_type": "code",
            "scope": scopes or self._settings.scopes,
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
                    "grant_type": "authorization_code",
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(f"Linear OAuth code exchange failed: {exc}", cause=exc) from exc

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise OAuthError("Linear OAuth response missing access_token")

        expires_at = compute_expires_at(data.get("expires_in"))
        workspace_id, workspace_name = self._resolve_identity(access_token)

        return OAuthTokens(
            access_token=access_token,
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            external_workspace_id=workspace_id,
            external_workspace_name=workspace_name,
        )

    def refresh(self, refresh_token: str) -> OAuthTokens:
        """A new access token and the NEW refresh token (the old one stops
        working). Identity fields are placeholders; the caller keeps its row's."""
        try:
            response = httpx.post(
                _TOKEN_URL,
                data={
                    "client_id": self._settings.client_id,
                    "client_secret": self._settings.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = oauth_error_code(exc)
            if code in CLIENT_ERRORS:
                # Linear answers a wrong client id or secret with 401 too: that is
                # this app's configuration, and reconnecting would not fix it.
                raise ConfigurationError(
                    f"Linear refused this app's OAuth client ({code}); the token was "
                    "not refreshed.", cause=exc,
                ) from exc
            if exc.response.status_code in (400, 401):
                raise OAuthReauthRequiredError(
                    "Linear refused the refresh token; reconnect Linear.", cause=exc
                ) from exc
            raise OAuthError(f"Linear OAuth token refresh failed: {exc}", cause=exc) from exc
        except httpx.HTTPError as exc:
            raise OAuthError(f"Linear OAuth token refresh failed: {exc}", cause=exc) from exc

        data = response.json()
        if not data.get("access_token"):
            raise OAuthError("Linear OAuth refresh response missing access_token")
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=compute_expires_at(data.get("expires_in")),
            external_workspace_id="",
            external_workspace_name=None,
        )

    def _resolve_identity(self, access_token: str) -> tuple[str, str | None]:
        """Resolve the token's Linear workspace via GraphQL (the token response names none)."""
        try:
            response = httpx.post(
                _GRAPHQL_URL,
                json={"query": "query { organization { id name } }"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise OAuthError(
                f"Linear OAuth identity resolution failed: {exc}", cause=exc
            ) from exc
        if payload.get("errors"):
            raise OAuthError(f"Linear OAuth identity resolution failed: {payload['errors']}")

        org = (payload.get("data") or {}).get("organization") or {}
        org_id = org.get("id")
        if not org_id:
            raise OAuthError("Linear GraphQL response missing organization.id")
        return org_id, org.get("name")
