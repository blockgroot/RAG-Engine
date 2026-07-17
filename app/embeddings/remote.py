"""Remote embedding provider for an OpenAI-compatible HTTP endpoint.

An alternative to the local backend for when you'd rather offload embedding to a
hosted service (e.g. DeepInfra serving BGE-M3). Same ``embed(...)`` contract.
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
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            response = self._client.embeddings.create(model=self.model, input=texts)
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
