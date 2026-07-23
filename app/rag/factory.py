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
    LLMSettings,
    MemorySettings,
    QueryUnderstandingSettings,
    RagSettings,
    RetrievalSettings,
    ReuseSettings,
    VerificationSettings,
    WebSearchSettings,
)
from ..core.exceptions import ProviderError
from ..embeddings import build_embedding_provider
from ..embeddings.base import EmbeddingProvider
from ..llm import build_llm_provider
from ..llm.base import LLMProvider
from ..memory import build_conversation_store
from ..memory.base import ConversationStore
from ..reranker import build_reranker
from ..vectorstore import build_vector_store
from ..vectorstore.base import VectorStore
from ..verification import build_verifier
from ..verification.base import Verifier
from ..websearch import build_web_search_provider
from ..websearch.base import WebSearchProvider
from .pipeline import RagPipeline
from .query_understanding import QueryUnderstander
from .retrieval import HybridRetriever

_UNSET = object()  # "argument omitted" vs an explicit None ("capability off")


def build_rag_pipeline(
    llm: LLMProvider | None = None,
    embedder: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
    settings: RagSettings | None = None,
    memory: ConversationStore | None = _UNSET,  # type: ignore[assignment]
    web_search: WebSearchProvider | None = _UNSET,  # type: ignore[assignment]
    retriever: HybridRetriever | None = _UNSET,  # type: ignore[assignment]
    query_understander: QueryUnderstander | None = _UNSET,  # type: ignore[assignment]
    verifier: Verifier | None = _UNSET,  # type: ignore[assignment]
) -> RagPipeline:
    """Build the RAG pipeline, defaulting each dependency from configuration."""
    web_settings = WebSearchSettings.from_env()
    rag_settings = settings or RagSettings.from_env()
    store = store or build_vector_store()
    llm_instance = llm or build_llm_provider()
    embedder_instance = embedder or build_embedding_provider()

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
        )

    if query_understander is _UNSET:
        # Phase 10: pre-retrieval normalization + semantic expansion (one LLM
        # call per fresh question) to fix the vocabulary-mismatch problem —
        # a question's phrasing and a document's own wording often diverge.
        query_understander = None
        qu_settings = QueryUnderstandingSettings.from_env()
        if qu_settings.enabled:
            qu_llm = llm_instance
            if qu_settings.model and qu_settings.model != LLMSettings.from_env().model:
                # An optional distinct (typically smaller/cheaper) model for this
                # bounded, structured task; reuses the main provider's key/base_url.
                main = LLMSettings.from_env()
                try:
                    qu_llm = build_llm_provider(
                        LLMSettings(
                            model=qu_settings.model,
                            api_key=main.api_key,
                            base_url=main.base_url,
                            timeout=main.timeout,
                        )
                    )
                except ProviderError:
                    qu_llm = llm_instance
            query_understander = QueryUnderstander(
                qu_llm, max_expansions=qu_settings.max_expansions
            )

    if verifier is _UNSET:
        # Phase 10: deterministic (non-LLM) post-generation faithfulness check,
        # reusing the same embedding provider instance (no extra model load).
        verifier = None
        v_settings = VerificationSettings.from_env()
        if v_settings.enabled:
            verifier = build_verifier(embedder_instance, v_settings)

    return RagPipeline(
        llm=llm_instance,
        embedder=embedder_instance,
        store=store,
        settings=rag_settings,
        memory=memory,
        web_search=web_search,
        memory_settings=MemorySettings.from_env(),
        web_search_settings=web_settings,
        retriever=retriever,
        reuse_settings=ReuseSettings.from_env(),
        query_understander=query_understander,
        verifier=verifier,
    )
