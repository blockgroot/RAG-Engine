"""Embedding provider wrapper around the OpenAI-compatible embeddings API."""

from __future__ import annotations

import os

from openai import OpenAI, APIError, APITimeoutError, APIConnectionError

from .exceptions import ConfigurationError, EmbeddingProviderError

# How long (seconds) to wait on a single API call before giving up.
DEFAULT_TIMEOUT = 60.0


class EmbeddingProvider:
    """Thin wrapper over an OpenAI-compatible embeddings endpoint.

    Configuration is read from constructor arguments, falling back to
    environment variables:

    - ``EMBEDDING_API_KEY``
    - ``EMBEDDING_BASE_URL``
    - ``EMBEDDING_MODEL``
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key or os.getenv("EMBEDDING_API_KEY")
        self.base_url = base_url or os.getenv("EMBEDDING_BASE_URL")
        self.model = model or os.getenv("EMBEDDING_MODEL")

        missing = [
            name
            for name, value in (
                ("EMBEDDING_API_KEY", self.api_key),
                ("EMBEDDING_BASE_URL", self.base_url),
                ("EMBEDDING_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                f"Missing required embedding configuration: {', '.join(missing)}"
            )

        self.timeout = timeout
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts and return one vector per input, in order.

        Raises ``EmbeddingProviderError`` on any timeout, connection, or API
        failure.
        """
        if not texts:
            return []

        try:
            response = self._client.embeddings.create(
                model=self.model,
                input=texts,
            )
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
