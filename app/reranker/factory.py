"""Single construction point for the reranker.

Local cross-encoders are process-wide singletons keyed by ``(model, device)``.
The default ``bge-reranker-v2-m3`` weights are ~2GB on disk and costly in RSS;
building a second copy for the workspace agent (or an in-API ingest path) is a
common cause of swap thrash on 16GB machines.

Remote backends (Jina ``/v1/rerank``) hold no weights — they are constructed
per settings and not cached as local models.
"""

from __future__ import annotations

from ..config.settings import RerankerSettings
from ..core.exceptions import ConfigurationError
from .base import Reranker
from .local import CrossEncoderReranker
from .remote import RemoteReranker

_local_cache: dict[tuple[str, str | None], CrossEncoderReranker] = {}


def clear_reranker_cache() -> None:
    """Drop cached local rerankers (tests / explicit process recycle)."""
    _local_cache.clear()


def build_reranker(settings: RerankerSettings | None = None) -> Reranker:
    """Build the configured reranker (local CrossEncoder or remote HTTP)."""
    settings = settings or RerankerSettings.from_env()

    if settings.backend == "remote":
        if not settings.api_key or not settings.base_url:
            raise ConfigurationError(
                "Remote reranker needs RERANKER_API_KEY (or EMBEDDING_API_KEY) "
                "and a base URL (RERANKER_BASE_URL / EMBEDDING_BASE_URL)"
            )
        return RemoteReranker(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
        )

    if settings.backend != "local":
        raise ConfigurationError(
            f"Unknown RERANKER_BACKEND: {settings.backend!r} "
            "(expected 'local' or 'remote')"
        )

    key = (settings.model, settings.device)
    cached = _local_cache.get(key)
    if cached is not None:
        return cached
    provider = CrossEncoderReranker(model=settings.model, device=settings.device)
    _local_cache[key] = provider
    return provider
