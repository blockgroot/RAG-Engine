"""The OAuth-provider contract admin-driven "Connect X" flows depend on.

An *OAuth provider* is any external system an org can grant this app access to
via a standard OAuth2 authorization-code flow — Notion now; Google/GitHub
later. Each gets a concrete implementation of this one interface, so
``app/api/auth.py`` can drive the connect flow for any of them without knowing
which provider it's talking to (identical shape to ``app/sources/base.py`` for
content adapters, and ``app/llm/base.py`` for the optional-capability pattern).

``refresh`` is an OPTIONAL capability (default ``NotImplementedError``, same
pattern as ``LLMProvider.generate_with_tools``): not every provider's OAuth
flow issues a refresh token (Notion's access tokens do not expire and have
none), so callers must be prepared for it to be unsupported.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def compute_expires_at(expires_in: object) -> datetime | None:
    """Turn an OAuth ``expires_in`` (seconds) into an absolute UTC deadline.

    Shared by every provider because they all receive the same field from the
    same wire format and were each carrying an identical private copy. Tolerant
    by design: providers legitimately omit ``expires_in`` for non-expiring
    tokens (Notion), and a malformed value should mean "unknown expiry" — which
    downstream treats as "do not proactively refresh" — rather than crashing a
    connect flow that otherwise succeeded.
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
        """Build the URL to redirect an admin's browser to for consent.

        ``state`` must be a caller-generated, single-use, short-TTL value
        stored server-side (not just signed) and re-validated in the callback
        to prevent CSRF/replay — see ``app/api/auth.py``.
        """
        raise NotImplementedError

    @abstractmethod
    def exchange_code(self, code: str) -> OAuthTokens:
        """Exchange a callback's authorization ``code`` for tokens.

        Implementations must raise ``core.exceptions.OAuthError`` on failure.
        """
        raise NotImplementedError

    def refresh(self, refresh_token: str) -> OAuthTokens:
        """Exchange a refresh token for a new access token.

        Optional capability — providers whose tokens don't expire (e.g.
        Notion) don't implement this. Raises ``NotImplementedError`` by
        default; callers must handle that, not assume every provider supports
        refresh.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support token refresh")
