"""Keyless web search via DuckDuckGo (the ``ddgs`` library).

The default: no key, no cost. Unofficial and rate-limited, so a better
provider can drop in behind the same interface. Every failure becomes a
``WebSearchError`` so the pipeline falls back cleanly.
"""

from __future__ import annotations

from ..core.exceptions import ConfigurationError, PrivacyError, WebSearchError
from .base import SearchResult, WebSearchProvider


class DuckDuckGoSearch(WebSearchProvider):
    """Web search backed by DuckDuckGo via the ``ddgs`` package."""

    def __init__(self) -> None:
        # Lazy import so the dependency is only needed when web search is used.
        try:
            from ddgs import DDGS  # noqa: F401
        except ImportError as exc:
            raise ConfigurationError(
                "ddgs is not installed. Run: pip install -r requirements.txt",
                cause=exc,
            ) from exc

    def search(
        self, query: str, max_results: int = 5, timeout: float = 8.0
    ) -> list[SearchResult]:
        from ddgs import DDGS

        from ..privacy import redact

        # Identifiers never reach the search engine readable (CLAUDE.md
        # "Identifiers leaving"): the masking provider had already restored the
        # model's query. A detector failure blocks the search, never sends unmasked.
        try:
            query = redact([query])[0]
        except PrivacyError as exc:
            raise WebSearchError("Web search skipped: masking failed.", cause=exc) from exc
        try:
            with DDGS(timeout=timeout) as ddgs:
                raw = ddgs.text(query, max_results=max_results)
        except Exception as exc:  # ratelimit, network, timeout, etc.
            raise WebSearchError(
                # Never log the query: it can carry a fact from a private answer.
                f"DuckDuckGo search failed: {exc}", cause=exc
            ) from exc

        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("href", ""),
                snippet=item.get("body", ""),
            )
            for item in (raw or [])
        ]
