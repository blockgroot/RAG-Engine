"""The embedding contract the rest of the app depends on.

Both the local (in-process) and remote (HTTP) implementations satisfy this
interface, so they are interchangeable (see ``factory.build_embedding_provider``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Abstract text-embedding provider."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts and return one vector per input, in order.

        Implementations must raise ``core.exceptions.EmbeddingProviderError`` on
        failure.
        """
        raise NotImplementedError
