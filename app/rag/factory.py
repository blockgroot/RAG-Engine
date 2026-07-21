"""Single construction point for the RAG pipeline.

Callers do ``build_rag_pipeline()`` and get a fully wired ``RagPipeline`` with the
LLM, embedding provider, vector store, conversation memory, and (if enabled) the
web-search tool built from configuration. Any dependency can be injected instead
(mainly for tests, which hold session-scoped fixtures) so nothing is constructed
twice.

``memory`` and ``web_search`` use a sentinel default so callers can explicitly
pass ``None`` to turn a capability OFF (e.g. the Phase 3 grounding tests build a
pure retrieve-gate-generate pipeline), distinct from omitting the argument (build
it from config).
"""

from __future__ import annotations

from ..config.settings import MemorySettings, RagSettings, WebSearchSettings
from ..core.exceptions import ProviderError
from ..embeddings import build_embedding_provider
from ..embeddings.base import EmbeddingProvider
from ..llm import build_llm_provider
from ..llm.base import LLMProvider
from ..memory import build_conversation_store
from ..memory.base import ConversationStore
from ..vectorstore import build_vector_store
from ..vectorstore.base import VectorStore
from ..websearch import build_web_search_provider
from ..websearch.base import WebSearchProvider
from .pipeline import RagPipeline

_UNSET = object()  # "argument omitted" vs an explicit None ("capability off")


def build_rag_pipeline(
    llm: LLMProvider | None = None,
    embedder: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
    settings: RagSettings | None = None,
    memory: ConversationStore | None = _UNSET,  # type: ignore[assignment]
    web_search: WebSearchProvider | None = _UNSET,  # type: ignore[assignment]
) -> RagPipeline:
    """Build the RAG pipeline, defaulting each dependency from configuration."""
    web_settings = WebSearchSettings.from_env()

    if memory is _UNSET:
        memory = build_conversation_store()

    if web_search is _UNSET:
        # Build the web-search tool only when enabled; degrade quietly if the
        # provider can't be constructed (e.g. missing optional dependency).
        web_search = None
        if web_settings.enabled:
            try:
                web_search = build_web_search_provider(web_settings)
            except ProviderError:
                web_search = None

    return RagPipeline(
        llm=llm or build_llm_provider(),
        embedder=embedder or build_embedding_provider(),
        store=store or build_vector_store(),
        settings=settings or RagSettings.from_env(),
        memory=memory,
        web_search=web_search,
        memory_settings=MemorySettings.from_env(),
        web_search_settings=web_settings,
    )
