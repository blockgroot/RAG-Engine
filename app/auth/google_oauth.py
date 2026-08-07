"""Google implementation of the ``OAuthProvider`` interface.

Uses Google's public OAuth2 endpoints directly via ``httpx`` (zero new
dependencies — Google Integration Plan decision D9, same dependency-light
reasoning as the rest of ``app/``). Differs from ``notion_oauth.py`` in three
structural ways:

1. **Token exchange is form-encoded, not JSON + HTTP Basic auth.** Google's
   token endpoint expects ``client_id``/``client_secret`` in the POST body
   (``data=``), not an ``Authorization`` header.
2. **Workspace identity isn't in the token response.** Notion's exchange
   returns a ``workspace_id``/``workspace_name`` directly; Google's does not,
   so a second call to the Drive ``about`` endpoint resolves who the token
   belongs to. We use the account's ``emailAddress`` for both
   ``external_workspace_id`` and ``external_workspace_name`` — it's stable,
   human-readable in the admin UI, and avoids requesting extra
   ``openid``/``email`` scopes or decoding an id_token just for identity.
3. **``refresh()`` is actually implemented here** — the first real
   implementation in this codebase (Notion tokens don't expire, so its
   ``refresh()`` stays the ABC's default ``NotImplementedError``). Google
   access tokens live ~1h; the refresh response omits a new
   ``refresh_token`` (Google's normal behavior) and has no workspace-identity
   fields, so ``external_workspace_id``/``external_workspace_name`` on a
   refreshed ``OAuthTokens`` are placeholders — the caller (the credentials
   layer, per decision D10) already knows the real identity from the existing
   ``oauth_connections`` row and only needs the refreshed access token.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from ..config.settings import GoogleSettings
from ..core.exceptions import ConfigurationError, OAuthError
from .base import OAuthProvider, OAuthTokens, compute_expires_at

_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_ABOUT_URL = "https://www.googleapis.com/drive/v3/about"
_TIMEOUT = 15.0


class GoogleOAuthProvider(OAuthProvider):
    """Drives Google's "Connect" OAuth flow for an internal-use OAuth client."""

    def __init__(self, settings: GoogleSettings | None = None) -> None:
        settings = settings or GoogleSettings.from_env()
        if not (settings.client_id and settings.client_secret and settings.redirect_uri):
            raise ConfigurationError(
                "Google OAuth requires GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and "
                "GOOGLE_REDIRECT_URI to be set (create an internal-use OAuth client "
                "in Google Cloud Console to obtain these)."
            )
        self._settings = settings

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "response_type": "code",
            "scope": self._settings.scopes,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
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
            raise OAuthError(f"Google OAuth code exchange failed: {exc}", cause=exc) from exc

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise OAuthError("Google OAuth response missing access_token")

        self._verify_granted_scopes(data.get("scope", ""))
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
        """Exchange a refresh token for a new access token.

        The response has no ``refresh_token`` (Google's normal behavior — the
        original one keeps working) and no workspace-identity fields, so
        ``external_workspace_id``/``external_workspace_name`` are placeholders
        here: the caller already knows the real identity from the existing
        ``oauth_connections`` row it's refreshing and only consumes the new
        access token + expiry.
        """
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
            body = {}
            try:
                body = exc.response.json()
            except ValueError:
                pass
            if body.get("error") == "invalid_grant":
                raise OAuthError(
                    "Google OAuth refresh failed with invalid_grant — the refresh "
                    "token has been revoked, expired, or rotated out; the admin "
                    "must reconnect Google Drive.",
                    cause=exc,
                ) from exc
            raise OAuthError(f"Google OAuth token refresh failed: {exc}", cause=exc) from exc
        except httpx.HTTPError as exc:
            raise OAuthError(f"Google OAuth token refresh failed: {exc}", cause=exc) from exc

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise OAuthError("Google OAuth refresh response missing access_token")

        return OAuthTokens(
            access_token=access_token,
            refresh_token=None,
            expires_at=compute_expires_at(data.get("expires_in")),
            external_workspace_id="",
            external_workspace_name=None,
        )

    def _resolve_identity(self, access_token: str) -> tuple[str, str | None]:
        """Resolve who the token belongs to via the Drive ``about`` endpoint.

        Google's token response carries no workspace/account identifier the
        way Notion's does, and ``OAuthTokens.external_workspace_id`` is NOT
        NULL, so we ask Drive directly rather than requesting extra
        ``openid``/``email`` scopes or decoding an id_token. ``emailAddress``
        is used for both id and name slots since it's the most human-readable
        identifier an admin will recognize in the connections UI.
        """
        try:
            response = httpx.get(
                _ABOUT_URL,
                params={"fields": "user(emailAddress,displayName,permissionId)"},
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(
                f"Google OAuth identity resolution failed: {exc}", cause=exc
            ) from exc

        user = response.json().get("user", {})
        email = user.get("emailAddress")
        if not email:
            raise OAuthError("Google Drive 'about' response missing user.emailAddress")
        return email, user.get("displayName") or email

    def _verify_granted_scopes(self, granted_scope: str) -> None:
        """Google can silently grant a subset of the requested scopes."""
        requested = set(self._settings.scopes.split())
        granted = set(granted_scope.split())
        missing = requested - granted
        if missing:
            raise OAuthError(
                "Google OAuth granted insufficient scope — missing: "
                f"{', '.join(sorted(missing))}. The admin must reconnect and "
                "approve all requested permissions."
            )

