"""Single construction point for the embedding provider.

Picks the local or remote backend from configuration and returns something
satisfying the ``EmbeddingProvider`` interface.
"""

from __future__ import annotations

from ..config.settings import EmbeddingSettings
from ..core.exceptions import ConfigurationError
from .base import EmbeddingProvider
from .local import LocalEmbeddingProvider
from .remote import RemoteEmbeddingProvider


def build_embedding_provider(
    settings: EmbeddingSettings | None = None,
) -> EmbeddingProvider:
    """Build the configured embedding provider (defaults to reading env vars)."""
    settings = settings or EmbeddingSettings.from_env()

    if settings.backend == "local":
        return LocalEmbeddingProvider(model=settings.model, device=settings.device)
    if settings.backend == "remote":
        return RemoteEmbeddingProvider(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
        )

    raise ConfigurationError(
        f"Unknown EMBEDDING_BACKEND: {settings.backend!r} (expected 'local' or 'remote')"
    )
