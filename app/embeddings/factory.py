"""Single construction point for the embedding provider.

Picks the local or remote backend from configuration and returns something
satisfying the ``EmbeddingProvider`` interface.

Local models are process-wide singletons keyed by ``(model, device)``. Loading
BGE-M3 twice (policy agent + workspace agent, or API + in-process ingest
worker) roughly doubles RSS and is enough to thrash a 16GB laptop into swap.
Remote providers stay uncached — they hold no heavy weights.
"""

from __future__ import annotations

from ..config.settings import EmbeddingSettings
from ..core.exceptions import ConfigurationError
from .base import EmbeddingProvider
from .local import LocalEmbeddingProvider
from .remote import RemoteEmbeddingProvider

# (model, device) -> provider. Cleared only via ``clear_embedding_provider_cache``
# (tests that must force a fresh load).
_local_cache: dict[tuple[str, str | None, int], LocalEmbeddingProvider] = {}


def clear_embedding_provider_cache() -> None:
    """Drop cached local embedders (tests / explicit process recycle)."""
    _local_cache.clear()


def build_embedding_provider(
    settings: EmbeddingSettings | None = None,
) -> EmbeddingProvider:
    """Build the configured embedding provider (defaults to reading env vars)."""
    settings = settings or EmbeddingSettings.from_env()

    if settings.backend == "local":
        key = (settings.model, settings.device, settings.batch_size)
        cached = _local_cache.get(key)
        if cached is not None:
            return cached
        provider = LocalEmbeddingProvider(
            model=settings.model, device=settings.device, batch_size=settings.batch_size
        )
        _local_cache[key] = provider
        return provider
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
