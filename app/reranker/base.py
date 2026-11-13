"""The reranker contract.

A cross-encoder re-scores retrieved candidates reading (query, chunk)
together and returns the best ``top_k``. It only reorders: each chunk keeps
its cosine ``score``, so the gate is unchanged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..vectorstore.base import RetrievedChunk


class Reranker(ABC):
    """Abstract cross-encoder reranker."""

    @abstractmethod
    def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]:
        """Return the ``top_k`` candidates most relevant to ``query``, reordered.
        Raises ``core.exceptions.ProviderError`` (or a subclass) on failure."""
        raise NotImplementedError
