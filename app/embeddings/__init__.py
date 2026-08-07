"""Text embedding access (local in-process or remote HTTP).

Public API:
    from app.embeddings import build_embedding_provider
    embedder = build_embedding_provider()
    vectors = embedder.embed(["hello world"])
"""

from .base import EmbeddingProvider
from .local import LocalEmbeddingProvider
from .remote import RemoteEmbeddingProvider
from .factory import build_embedding_provider, clear_embedding_provider_cache

__all__ = [
    "EmbeddingProvider",
    "LocalEmbeddingProvider",
    "RemoteEmbeddingProvider",
    "build_embedding_provider",
    "clear_embedding_provider_cache",
]
