"""The web-search contract (Phase 5).

Used only as the *fallback* tool when internal retrieval fails the confidence
gate AND the model judges the question to be about a real, named external entity
(see app/rag/pipeline.py). Behind an interface + factory like every capability,
so DuckDuckGo (keyless default) can be swapped for Tavily/Brave via config.

Implementations MUST honour ``timeout`` and raise ``core.exceptions.WebSearchError``
on failure/timeout, so the pipeline can degrade cleanly to the internal fallback.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResult:
    """A single web result, trimmed to what an LLM needs to compose an answer."""

    title: str
    url: str
    snippet: str


class WebSearchProvider(ABC):
    """Abstract web-search tool."""

    @abstractmethod
    def search(
        self, query: str, max_results: int = 5, timeout: float = 8.0
    ) -> list[SearchResult]:
        """Run one search and return up to ``max_results`` results.

        Must raise ``core.exceptions.WebSearchError`` on failure or timeout.
        """
        raise NotImplementedError
