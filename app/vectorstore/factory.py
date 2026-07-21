"""Single construction point for the vector store.

Callers do ``build_vector_store()`` and get back something satisfying the
``VectorStore`` interface. Adding another backend later (e.g. a managed vector
DB) means adding a branch here — callers don't change.
"""

from __future__ import annotations

from ..config.settings import DatabaseSettings, VectorStoreSettings
from ..core.exceptions import ConfigurationError
from .base import VectorStore
from .pgvector_store import PgVectorStore


def build_vector_store(
    settings: VectorStoreSettings | None = None,
    db_settings: DatabaseSettings | None = None,
) -> VectorStore:
    """Build the configured vector store (defaults to reading env vars)."""
    settings = settings or VectorStoreSettings.from_env()

    if settings.backend == "pgvector":
        return PgVectorStore(settings=db_settings)

    raise ConfigurationError(
        f"Unknown VECTOR_STORE_BACKEND: {settings.backend!r} (expected 'pgvector')"
    )
