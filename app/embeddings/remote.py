"""Remote embedding provider for an OpenAI-compatible HTTP endpoint.

Same ``embed(...)`` contract as the local backend, batched at
``EMBED_BATCH_SIZE`` so a huge document is never one unbounded payload.
"""

from __future__ import annotations

from openai import OpenAI, APIError, APITimeoutError, APIConnectionError

from ..core.exceptions import ConfigurationError, EmbeddingProviderError, PrivacyError
from ..privacy import redact
from .base import EmbeddingProvider

DEFAULT_TIMEOUT = 60.0


class RemoteEmbeddingProvider(EmbeddingProvider):
    """Embed text via a remote OpenAI-compatible endpoint. Build via
    ``factory.build_embedding_provider``."""

    def __init__(
        self,
        model: str | None,
        api_key: str | None,
        base_url: str | None,
        timeout: float = DEFAULT_TIMEOUT,
        batch_size: int = 16,
        dimensions: int | None = None,
    ) -> None:
        missing = [
            name
            for name, value in (
                ("EMBEDDING_MODEL", model),
                ("EMBEDDING_API_KEY", api_key),
                ("EMBEDDING_BASE_URL", base_url),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                f"Missing required embedding configuration: {', '.join(missing)}"
            )

        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.batch_size = max(1, batch_size)
        self.dimensions = dimensions
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        # Identifiers are masked before leaving: a vector never needs the digits,
        # and exact-number search is the keyword leg's job.
        try:
            batch = redact(batch)
        except PrivacyError as exc:
            raise EmbeddingProviderError(str(exc), cause=exc) from exc
        try:
            # Sent only when asked for: a provider without it rejects the call.
            extra = {"dimensions": self.dimensions} if self.dimensions else {}
            response = self._client.embeddings.create(model=self.model, input=batch, **extra)
        except APITimeoutError as exc:
            raise EmbeddingProviderError(
                f"Embedding request timed out after {self.timeout}s", cause=exc
            ) from exc
        except APIConnectionError as exc:
            raise EmbeddingProviderError(
                f"Could not connect to embedding endpoint at {self.base_url}",
                cause=exc,
            ) from exc
        except APIError as exc:
            raise EmbeddingProviderError(
                f"Embedding API error: {exc}", cause=exc
            ) from exc

        # Items may come back out of order: sort by index.
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # Batched so many chunks never become one unbounded payload.
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            out.extend(self._embed_batch(texts[start : start + self.batch_size]))
        return out
