"""Single construction point for the web-search provider.

Defaults to keyless DuckDuckGo. Adding Tavily/Brave later means a branch here
(keyed on ``WEB_SEARCH_PROVIDER``) — callers don't change.
"""

from __future__ import annotations

from ..config.settings import WebSearchSettings
from ..core.exceptions import ConfigurationError
from .base import WebSearchProvider
from .duckduckgo import DuckDuckGoSearch


def build_web_search_provider(
    settings: WebSearchSettings | None = None,
) -> WebSearchProvider:
    """Build the configured web-search provider (defaults to DuckDuckGo)."""
    settings = settings or WebSearchSettings.from_env()

    if settings.provider == "duckduckgo":
        return DuckDuckGoSearch()

    raise ConfigurationError(
        f"Unknown WEB_SEARCH_PROVIDER: {settings.provider!r} "
        "(supported: 'duckduckgo'; 'tavily' reserved for a future provider)"
    )
