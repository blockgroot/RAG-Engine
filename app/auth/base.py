"""The OAuth-provider contract the admin "Connect X" flows depend on.

``refresh`` is optional (default ``NotImplementedError``): some providers
(Notion) issue no refresh token, so callers must handle it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def compute_expires_at(expires_in: object) -> datetime | None:
    """Turn an OAuth ``expires_in`` (seconds) into an absolute UTC deadline.

    Missing or malformed means ``None`` (unknown expiry, no proactive refresh),
    never a failed connect.
    """
    if expires_in is None:
        return None
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@dataclass(frozen=True)
class OAuthTokens:
    """The result of a successful authorize/exchange (or refresh) call."""

    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    external_workspace_id: str
    external_workspace_name: str | None = None


class OAuthProvider(ABC):
    """Abstract OAuth2 authorization-code flow for one external provider."""

    @abstractmethod
    def authorize_url(self, state: str) -> str:
        """Build the consent URL for the admin's browser.

        ``state`` must be single-use, short-lived and stored server-side, then
        re-checked in the callback (CSRF/replay).
        """
        raise NotImplementedError

    @abstractmethod
    def exchange_code(self, code: str) -> OAuthTokens:
        """Exchange a callback ``code`` for tokens; raise ``OAuthError`` on failure."""
        raise NotImplementedError

    def refresh(self, refresh_token: str) -> OAuthTokens:
        """Exchange a refresh token for a new access token.

        Optional: raises ``NotImplementedError`` for providers whose tokens do not expire.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support token refresh")


#: OAuth token-endpoint errors that blame the CLIENT (this app's id or secret),
#: not the person's grant (RFC 6749 §5.2). Reconnecting cannot fix them.
CLIENT_ERRORS = frozenset({"invalid_client", "unauthorized_client"})


def oauth_error_code(exc) -> str | None:
    """The ``error`` field of a token endpoint's JSON error body, if any."""
    try:
        body = exc.response.json()
    except (ValueError, AttributeError):
        return None
    return body.get("error") if isinstance(body, dict) else None
