"""Linear implementation of the ``OAuthProvider`` interface.

Structurally closest to ``google_oauth.py``: Linear's token response carries
no workspace identity the way Notion's does, so a follow-up GraphQL call
(``query { organization { id name } } ``) resolves who the token belongs to.
Unlike Google, a standard Linear OAuth app issues an access token that does
not expire and no refresh token — same non-expiring shape as Notion/Slack —
so ``refresh()`` stays the ABC's default ``NotImplementedError``.

This is a SEPARATE, non-fallback-linked credential path from the legacy
``LINEAR_TOKEN_<NAME>`` personal-API-key path (``LinearSettings`` in
``app/config/settings.py``) — same coexistence Notion has between its OAuth
flow and ``NOTION_TOKEN_<NAME>``. A token obtained here is passed to
``LinearAdapter`` as an already-resolved OAuth token (``oauth=True``), which
matters because Linear sends the ``Authorization`` header differently for
the two credential types (see ``app/sources/linear.py``).
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from ..config.settings import LinearSettings
from ..core.exceptions import ConfigurationError, OAuthError
from .base import OAuthProvider, OAuthTokens, compute_expires_at

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

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "response_type": "code",
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

    def _resolve_identity(self, access_token: str) -> tuple[str, str | None]:
        """Resolve which Linear workspace the token belongs to.

        Linear's token response carries no workspace identifier, so this asks
        the GraphQL API directly (same reasoning as Google's Drive ``about``
        call in ``google_oauth.py``) rather than decoding anything extra out
        of the token itself.
        """
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
