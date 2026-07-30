"""Single construction point for an OAuth provider.

Callers do ``build_oauth_provider(provider)`` and get back something
satisfying the ``OAuthProvider`` interface. Adding Google/GitHub later means
adding a branch here — callers (``app/api/auth.py``) don't change. Same shape
as ``app/sources/factory.py``.
"""

from __future__ import annotations

from ..core.exceptions import ConfigurationError
from .base import OAuthProvider
from .google_oauth import GoogleOAuthProvider
from .notion_oauth import NotionOAuthProvider

DEFAULT_OAUTH_PROVIDER = "notion"


def build_oauth_provider(provider: str = DEFAULT_OAUTH_PROVIDER) -> OAuthProvider:
    """Build the configured OAuth provider (defaults to Notion)."""
    provider = (provider or DEFAULT_OAUTH_PROVIDER).lower()

    if provider == "notion":
        return NotionOAuthProvider()
    elif provider == "google":
        return GoogleOAuthProvider()

    raise ConfigurationError(f"Unknown OAuth provider: {provider!r} (expected 'notion' or 'google')")
