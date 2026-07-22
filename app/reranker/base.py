"""The reranker contract (Phase 6).

A reranker re-scores an already-retrieved candidate set with a cross-encoder that
reads the (query, chunk) pair *together* — far more precise than the bi-encoder
cosine used for first-stage retrieval — and returns the best ``top_k`` in the new
order. Behind an interface + factory like every capability, so the model can be
swapped via config.

Reranking only reorders/selects; it preserves each ``RetrievedChunk`` (including
its original cosine ``score``), so the confidence gate downstream still sees the
same cosine similarity it always did.
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

        Must raise ``core.exceptions.ProviderError`` (or a subclass) on failure.
        """
        raise NotImplementedError
