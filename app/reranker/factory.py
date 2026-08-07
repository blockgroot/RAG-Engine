"""Single construction point for the reranker.

Local cross-encoders are process-wide singletons keyed by ``(model, device)``.
The default ``bge-reranker-v2-m3`` weights are ~2GB on disk and costly in RSS;
building a second copy for the workspace agent (or an in-API ingest path) is a
common cause of swap thrash on 16GB machines.
"""

from __future__ import annotations

from ..config.settings import RerankerSettings
from .base import Reranker
from .local import CrossEncoderReranker

_local_cache: dict[tuple[str, str | None], CrossEncoderReranker] = {}


def clear_reranker_cache() -> None:
    """Drop cached local rerankers (tests / explicit process recycle)."""
    _local_cache.clear()


def build_reranker(settings: RerankerSettings | None = None) -> Reranker:
    """Build the configured reranker (local cross-encoder by default)."""
    settings = settings or RerankerSettings.from_env()
    key = (settings.model, settings.device)
    cached = _local_cache.get(key)
    if cached is not None:
        return cached
    provider = CrossEncoderReranker(model=settings.model, device=settings.device)
    _local_cache[key] = provider
    return provider
