"""Remote embedding provider for an OpenAI-compatible HTTP endpoint.

An alternative to the local backend for when you'd rather offload embedding to a
hosted service (e.g. DeepInfra serving BGE-M3). Same ``embed(...)`` contract.

``embed()`` batches requests at ``EMBED_BATCH_SIZE`` (default 16) for the same
reason ``LocalEmbeddingProvider`` does: a document with an unusually large
number of chunks must not turn into one unbounded request/response payload —
a real incident on a memory-constrained deploy (Render free, 512MB) traced
back to exactly this gap, since this backend originally sent the entire chunk
list in a single call with no cap.
"""

from __future__ import annotations

from openai import OpenAI, APIError, APITimeoutError, APIConnectionError

from ..core.exceptions import ConfigurationError, EmbeddingProviderError
from .base import EmbeddingProvider

DEFAULT_TIMEOUT = 60.0


class RemoteEmbeddingProvider(EmbeddingProvider):
    """Embed text via a remote OpenAI-compatible embeddings endpoint.

    Prefer building this via ``factory.build_embedding_provider`` so config comes
    from a single place.
    """

    def __init__(
        self,
        model: str | None,
        api_key: str | None,
        base_url: str | None,
        timeout: float = DEFAULT_TIMEOUT,
        batch_size: int = 16,
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
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        try:
            response = self._client.embeddings.create(model=self.model, input=batch)
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

        # The API may return items out of order; sort by index to be safe.
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # Batch so a document with an unusually large number of chunks cannot
        # build one unbounded request/response payload in a single call — the
        # same reasoning as LocalEmbeddingProvider.embed()'s batching loop,
        # which this backend was missing (EMBED_BATCH_SIZE's "avoids OOM on
        # large docs" intent only held for the local backend until now).
        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            out.extend(self._embed_batch(texts[start : start + self.batch_size]))
        return out
