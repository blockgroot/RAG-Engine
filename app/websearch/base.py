"""The web-search contract.

Used only as the fallback when retrieval fails the gate and the model
judges the question to be about a named external entity. Implementations
MUST honour ``timeout`` and raise ``core.exceptions.WebSearchError``.
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
        """Run one search and return up to ``max_results`` results. Raises
        ``core.exceptions.WebSearchError`` on failure or timeout."""
        raise NotImplementedError
