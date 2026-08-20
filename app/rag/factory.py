"""Single construction point for the RAG pipeline.

Callers do ``build_rag_pipeline()`` and get a fully wired ``RagPipeline`` with the
LLM, embedding provider, vector store, conversation memory, web-search tool, and
(Phase 6) a hybrid + reranking retriever — all built from configuration. Any
dependency can be injected instead (mainly for tests, which hold session-scoped
fixtures) so nothing is constructed twice.

``memory``, ``web_search`` and ``retriever`` use a sentinel default so callers can
explicitly pass ``None`` to turn a capability OFF (e.g. the Phase 3 grounding
tests build a pure retrieve-gate-generate pipeline), distinct from omitting the
argument (build it from config).
"""

from __future__ import annotations

from ..config.settings import (
    DecomposeSettings,
    MemorySettings,
    RagSettings,
    QueryNormSettings,
    RecoverySettings,
    ToneSettings,
    RetrievalSettings,
    ReuseSettings,
    WebSearchSettings,
)
from ..core.exceptions import ProviderError
from ..embeddings import build_embedding_provider
from ..embeddings.base import EmbeddingProvider
from ..llm import build_aux_llm_provider, build_llm_provider
from ..llm.base import LLMProvider
from ..memory import build_conversation_store
from ..memory.base import ConversationStore
from ..reranker import build_reranker
from ..vectorstore import build_vector_store
from ..vectorstore.base import VectorStore
from ..websearch import build_web_search_provider
from ..websearch.base import WebSearchProvider
from .pipeline import RagPipeline
from .prompts import PromptProfile
from .retrieval import HybridRetriever

_UNSET = object()  # "argument omitted" vs an explicit None ("capability off")


def build_rag_pipeline(
    llm: LLMProvider | None = None,
    llm_aux: LLMProvider | None = None,
    embedder: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
    settings: RagSettings | None = None,
    memory: ConversationStore | None = _UNSET,  # type: ignore[assignment]
    web_search: WebSearchProvider | None = _UNSET,  # type: ignore[assignment]
    retriever: HybridRetriever | None = _UNSET,  # type: ignore[assignment]
    recovery_settings: RecoverySettings | None = None,
    tone_settings: ToneSettings | None = None,
    query_norm_settings: QueryNormSettings | None = None,
    prompt_profile: PromptProfile | None = None,
    source_provider: str | None = None,
) -> RagPipeline:
    """Build the RAG pipeline, defaulting each dependency from configuration."""
    web_settings = WebSearchSettings.from_env()
    rag_settings = settings or RagSettings.from_env()
    store = store or build_vector_store()
    llm_main = llm or build_llm_provider()
    llm_aux_impl = llm_aux or (build_aux_llm_provider() if llm is None else llm_main)

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

    if retriever is _UNSET:
        retrieval_settings = RetrievalSettings.from_env()
        reranker = None
        if retrieval_settings.rerank_enabled:
            try:
                reranker = build_reranker()
            except ProviderError:
                reranker = None
        retriever = HybridRetriever(
            store=store,
            reranker=reranker,
            settings=retrieval_settings,
            rag_settings=rag_settings,
            source_provider=source_provider,
        )

    return RagPipeline(
        llm=llm_main,
        llm_aux=llm_aux_impl,
        embedder=embedder or build_embedding_provider(),
        store=store,
        settings=rag_settings,
        memory=memory,
        web_search=web_search,
        memory_settings=MemorySettings.from_env(),
        web_search_settings=web_settings,
        retriever=retriever,
        reuse_settings=ReuseSettings.from_env(),
        recovery_settings=recovery_settings or RecoverySettings.from_env(),
        tone_settings=tone_settings or ToneSettings.from_env(),
        decompose_settings=DecomposeSettings.from_env(),
        query_norm_settings=query_norm_settings or QueryNormSettings.from_env(),
        prompt_profile=prompt_profile,
        source_provider=source_provider,
    )
