"""Single construction point for the RAG pipeline.

Callers do ``build_rag_pipeline()`` and get a fully wired ``RagPipeline`` with the
LLM, embedding provider, and vector store built from configuration. Providers can
be injected instead (mainly for tests, which already hold session-scoped provider
fixtures) so nothing is constructed twice.
"""

from __future__ import annotations

from ..config.settings import RagSettings
from ..embeddings import build_embedding_provider
from ..embeddings.base import EmbeddingProvider
from ..llm import build_llm_provider
from ..llm.base import LLMProvider
from ..vectorstore import build_vector_store
from ..vectorstore.base import VectorStore
from .pipeline import RagPipeline


def build_rag_pipeline(
    llm: LLMProvider | None = None,
    embedder: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
    settings: RagSettings | None = None,
) -> RagPipeline:
    """Build the RAG pipeline, defaulting each dependency from configuration."""
    return RagPipeline(
        llm=llm or build_llm_provider(),
        embedder=embedder or build_embedding_provider(),
        store=store or build_vector_store(),
        settings=settings or RagSettings.from_env(),
    )
