"""Google ``OAuthProvider`` over plain ``httpx``.

The exchange is form-encoded (client id/secret in the body). The token
response names no account, so the Drive ``about`` email is both the
workspace id and name. Access tokens last ~1h; a refresh returns no new
refresh token and no identity, so those fields are placeholders there.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from ..config.settings import GoogleSettings
from ..core.exceptions import ConfigurationError, OAuthError
from .base import CLIENT_ERRORS, OAuthProvider, OAuthTokens, compute_expires_at, oauth_error_code

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
                "Your company's Google app isn't set up yet. An admin adds it under "
                "Sources → Google Drive → Set up your Google app (self-hosted: "
                "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI in .env)."
            )
        self._settings = settings

    def authorize_url(self, state: str, *, include_granted: bool = True) -> str:
        """``include_granted=False`` keeps a token to exactly the requested
        scopes: Act's per-person ``drive.file`` token must not pick up a
        broader scope the same person once granted this client as an admin."""
        params = {
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "response_type": "code",
            "scope": self._settings.scopes,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true" if include_granted else "false",
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

        Google returns no new refresh token (the old one keeps working) and no
        identity, so the workspace fields are placeholders; callers read only the
        token and expiry.
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
            code = oauth_error_code(exc)
            if code in CLIENT_ERRORS:
                # This app's client id or secret is wrong for the token: a host
                # (or a company app) misconfiguration, not the person's grant.
                raise ConfigurationError(
                    f"Google refused this app's OAuth client ({code}); the token was "
                    "not refreshed.", cause=exc,
                ) from exc
            if code == "invalid_grant":
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
        """Resolve the token's account via Drive ``about``; its email is id and name.

        Avoids extra ``openid``/``email`` scopes just for identity.
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

