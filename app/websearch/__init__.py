"""Web search tool (Phase 5).

Public API:
    from app.websearch import build_web_search_provider
    ws = build_web_search_provider()
    results = ws.search("Cigna health insurance", max_results=5, timeout=8.0)
"""

from .base import WebSearchProvider, SearchResult
from .duckduckgo import DuckDuckGoSearch
from .factory import build_web_search_provider

__all__ = [
    "WebSearchProvider",
    "SearchResult",
    "DuckDuckGoSearch",
    "build_web_search_provider",
]
