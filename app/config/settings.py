"""Typed configuration, read from environment variables in one place.

Nothing else in the app calls ``os.getenv`` for provider config — factories take
these settings objects, so there is a single, documented source of truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_TIMEOUT = 60.0
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_EMBEDDING_BACKEND = "local"

# Output dimension of the current embedding model (BGE-M3 = 1024). The DB schema
# declares vector(EMBEDDING_DIM); the two MUST stay in sync — see CLAUDE.md.
DEFAULT_EMBEDDING_DIM = 1024

# Chunking defaults (characters). Reasoning documented in app/ingestion/chunking.py.
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 150

DEFAULT_VECTOR_STORE_BACKEND = "pgvector"

# RAG query-path defaults. See app/rag/pipeline.py for the reasoning behind the
# similarity threshold value and the two-layer grounding design.
DEFAULT_RAG_TOP_K = 5
DEFAULT_RAG_SIMILARITY_THRESHOLD = 0.35
DEFAULT_RAG_FALLBACK_RESPONSE = (
    "I don't have information on that in the available policy documents."
)

# Connection-pool sizing for the Postgres backing store.
DEFAULT_DB_POOL_MIN_SIZE = 1
DEFAULT_DB_POOL_MAX_SIZE = 10

# External content sources (Phase 4). Only "notion" exists so far.
DEFAULT_SOURCE_TYPE = "notion"

# Conversation memory (Phase 5). A "turn" is one question + its answer.
# recent_turns are kept verbatim; older turns are compressed once the total
# exceeds summarize_after. See app/rag/pipeline.py for the reasoning.
DEFAULT_MEMORY_RECENT_TURNS = 4
DEFAULT_MEMORY_SUMMARIZE_AFTER = 6

# Retrieval improvements (Phase 6): contextual retrieval (ingest-time),
# hybrid search + cross-encoder reranking (query-time). See app/rag/retrieval.py
# and CLAUDE.md §2/§4 for the reasoning behind each value.
DEFAULT_CONTEXTUAL_ENABLED = True          # prepend LLM context to each chunk at ingest
DEFAULT_RETRIEVAL_HYBRID_ENABLED = True    # fuse vector + keyword (BM25-style) search
DEFAULT_RETRIEVAL_RERANK_ENABLED = True    # cross-encoder rerank of the candidate pool
DEFAULT_RETRIEVAL_CANDIDATE_POOL = 30      # how many candidates to fetch/rerank before top_k
DEFAULT_RETRIEVAL_RRF_K = 60               # Reciprocal Rank Fusion constant (standard default)
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

# Web search tool (Phase 5). Keyless DuckDuckGo by default (no paid dependency);
# set WEB_SEARCH_PROVIDER=tavily + WEB_SEARCH_API_KEY for production quality.
DEFAULT_WEB_SEARCH_ENABLED = True
DEFAULT_WEB_SEARCH_PROVIDER = "duckduckgo"
DEFAULT_WEB_SEARCH_MAX_RESULTS = 5
DEFAULT_WEB_SEARCH_TIMEOUT = 8.0


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class LLMSettings:
    """Configuration for the LLM provider.

    - ``model``    provider/model string, e.g. ``openai/auto`` or
      ``anthropic/claude-sonnet-5`` (required)
    - ``api_key``  optional explicit key (else provider's standard env var)
    - ``base_url`` optional; for OpenAI-compatible / self-hosted endpoints
    """

    model: str | None
    api_key: str | None
    base_url: str | None
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "LLMSettings":
        return cls(
            model=os.getenv("LLM_MODEL"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
            timeout=float(os.getenv("LLM_TIMEOUT") or DEFAULT_TIMEOUT),
        )


@dataclass(frozen=True)
class EmbeddingSettings:
    """Configuration for the embedding provider.

    - ``backend``  ``local`` (in-process sentence-transformers) or ``remote``
      (HTTP OpenAI-compatible endpoint)
    - ``model``    embedding model id
    - ``device``   optional device for the local backend (cpu/cuda/mps)
    - ``api_key`` / ``base_url``  used only by the remote backend
    """

    backend: str
    model: str | None
    device: str | None
    api_key: str | None
    base_url: str | None
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "EmbeddingSettings":
        return cls(
            backend=(os.getenv("EMBEDDING_BACKEND") or DEFAULT_EMBEDDING_BACKEND).lower(),
            model=os.getenv("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL,
            device=os.getenv("EMBEDDING_DEVICE") or None,
            api_key=os.getenv("EMBEDDING_API_KEY"),
            base_url=os.getenv("EMBEDDING_BASE_URL"),
            timeout=float(os.getenv("EMBEDDING_TIMEOUT") or DEFAULT_TIMEOUT),
        )


@dataclass(frozen=True)
class DatabaseSettings:
    """Configuration for the Postgres/pgvector backing store.

    - ``url``  standard libpq connection string, e.g.
      ``postgresql://user:pass@host:5432/dbname``
    - ``embedding_dim``  vector dimension the ``chunks.embedding`` column uses;
      must match the embedding model's output (BGE-M3 = 1024)
    """

    url: str | None
    embedding_dim: int = DEFAULT_EMBEDDING_DIM
    pool_min_size: int = DEFAULT_DB_POOL_MIN_SIZE
    pool_max_size: int = DEFAULT_DB_POOL_MAX_SIZE

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        return cls(
            url=os.getenv("DATABASE_URL"),
            embedding_dim=int(os.getenv("EMBEDDING_DIM") or DEFAULT_EMBEDDING_DIM),
            pool_min_size=int(os.getenv("DB_POOL_MIN_SIZE") or DEFAULT_DB_POOL_MIN_SIZE),
            pool_max_size=int(os.getenv("DB_POOL_MAX_SIZE") or DEFAULT_DB_POOL_MAX_SIZE),
        )


@dataclass(frozen=True)
class ChunkingSettings:
    """Configuration for document chunking (sizes measured in characters)."""

    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP

    @classmethod
    def from_env(cls) -> "ChunkingSettings":
        return cls(
            chunk_size=int(os.getenv("CHUNK_SIZE") or DEFAULT_CHUNK_SIZE),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP") or DEFAULT_CHUNK_OVERLAP),
        )


@dataclass(frozen=True)
class VectorStoreSettings:
    """Configuration for the vector store abstraction layer."""

    backend: str = DEFAULT_VECTOR_STORE_BACKEND

    @classmethod
    def from_env(cls) -> "VectorStoreSettings":
        return cls(
            backend=(os.getenv("VECTOR_STORE_BACKEND") or DEFAULT_VECTOR_STORE_BACKEND).lower(),
        )


@dataclass(frozen=True)
class RagSettings:
    """Configuration for the RAG query path (retrieve -> gate -> generate).

    - ``top_k``  how many chunks to retrieve per question.
    - ``similarity_threshold``  minimum cosine similarity (in [0, 1]) the *best*
      retrieved chunk must clear before the LLM is called at all. Below it, we
      short-circuit to ``fallback_response`` and never invoke the model. This is
      the cheap first line of defence against answering from irrelevant context;
      the strict prompt (see ``app/rag/prompts.py``) is the second. Reasoning for
      the default value lives in ``app/rag/pipeline.py``.
    - ``fallback_response``  the single, fixed "I don't know" string. It is used
      in three places that MUST agree: the confidence gate, the LLM's refusal
      instruction, and the pipeline's refusal detection — so it lives here as one
      source of truth rather than being duplicated.
    """

    top_k: int = DEFAULT_RAG_TOP_K
    similarity_threshold: float = DEFAULT_RAG_SIMILARITY_THRESHOLD
    fallback_response: str = DEFAULT_RAG_FALLBACK_RESPONSE

    @classmethod
    def from_env(cls) -> "RagSettings":
        return cls(
            top_k=int(os.getenv("RAG_TOP_K") or DEFAULT_RAG_TOP_K),
            similarity_threshold=float(
                os.getenv("RAG_SIMILARITY_THRESHOLD") or DEFAULT_RAG_SIMILARITY_THRESHOLD
            ),
            fallback_response=os.getenv("RAG_FALLBACK_RESPONSE") or DEFAULT_RAG_FALLBACK_RESPONSE,
        )


@dataclass(frozen=True)
class NotionSettings:
    """Configuration for the Notion content source (Phase 4).

    - ``token``  the auth token the adapter uses. For this phase that is a Notion
      *internal integration secret* (a single static token) — the simplest viable
      auth given there is no web app yet to host an OAuth consent redirect.
    - ``client_id`` / ``client_secret`` / ``redirect_uri``  OAuth app credentials,
      read here so they aren't hardcoded and are ready for the later multi-tenant
      OAuth phase. They are NOT used by the adapter yet — ``token`` is.
    """

    token: str | None
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None

    @classmethod
    def from_env(cls) -> "NotionSettings":
        return cls(
            token=os.getenv("NOTION_TOKEN"),
            client_id=os.getenv("NOTION_CLIENT_ID"),
            client_secret=os.getenv("NOTION_CLIENT_SECRET"),
            redirect_uri=os.getenv("NOTION_REDIRECT_URI"),
        )


@dataclass(frozen=True)
class MemorySettings:
    """Conversation-memory sizing (Phase 5).

    - ``recent_turns``     how many recent turns to keep verbatim in context.
    - ``summarize_after``  once a conversation exceeds this many turns, the older
      ones (all but ``recent_turns``) are compressed into a running summary.
    """

    recent_turns: int = DEFAULT_MEMORY_RECENT_TURNS
    summarize_after: int = DEFAULT_MEMORY_SUMMARIZE_AFTER

    @classmethod
    def from_env(cls) -> "MemorySettings":
        return cls(
            recent_turns=int(os.getenv("MEMORY_RECENT_TURNS") or DEFAULT_MEMORY_RECENT_TURNS),
            summarize_after=int(
                os.getenv("MEMORY_SUMMARIZE_AFTER") or DEFAULT_MEMORY_SUMMARIZE_AFTER
            ),
        )


@dataclass(frozen=True)
class WebSearchSettings:
    """Web-search tool configuration (Phase 5).

    - ``enabled``      whether the RAG pipeline offers the web-search tool at all.
    - ``provider``     ``duckduckgo`` (keyless, default) or ``tavily`` (needs key).
    - ``api_key``      required only by providers that need one (e.g. Tavily).
    - ``max_results``  how many results to fetch and feed back to the model.
    - ``timeout``      hard cap (seconds) on the search call; on timeout the
      pipeline degrades to the fixed internal fallback.
    """

    enabled: bool = DEFAULT_WEB_SEARCH_ENABLED
    provider: str = DEFAULT_WEB_SEARCH_PROVIDER
    api_key: str | None = None
    max_results: int = DEFAULT_WEB_SEARCH_MAX_RESULTS
    timeout: float = DEFAULT_WEB_SEARCH_TIMEOUT

    @classmethod
    def from_env(cls) -> "WebSearchSettings":
        return cls(
            enabled=_env_bool("WEB_SEARCH_ENABLED", DEFAULT_WEB_SEARCH_ENABLED),
            provider=(os.getenv("WEB_SEARCH_PROVIDER") or DEFAULT_WEB_SEARCH_PROVIDER).lower(),
            api_key=os.getenv("WEB_SEARCH_API_KEY"),
            max_results=int(os.getenv("WEB_SEARCH_MAX_RESULTS") or DEFAULT_WEB_SEARCH_MAX_RESULTS),
            timeout=float(os.getenv("WEB_SEARCH_TIMEOUT") or DEFAULT_WEB_SEARCH_TIMEOUT),
        )


@dataclass(frozen=True)
class ContextualSettings:
    """Contextual-retrieval config (Phase 6, ingest-time).

    When enabled, a short LLM-generated context is prepended to each chunk before
    it is embedded and stored, so the chunk carries its surrounding meaning.
    """

    enabled: bool = DEFAULT_CONTEXTUAL_ENABLED

    @classmethod
    def from_env(cls) -> "ContextualSettings":
        return cls(enabled=_env_bool("INGEST_CONTEXTUAL_ENABLED", DEFAULT_CONTEXTUAL_ENABLED))


@dataclass(frozen=True)
class RetrievalSettings:
    """Query-time retrieval config (Phase 6).

    - ``hybrid_enabled``   fuse keyword (BM25-style) results with vector results.
    - ``rerank_enabled``   cross-encoder rerank the fused candidate pool.
    - ``candidate_pool``   how many candidates to fetch (per signal) and rerank
      before selecting the final ``RagSettings.top_k``.
    - ``rrf_k``            Reciprocal Rank Fusion constant.
    """

    hybrid_enabled: bool = DEFAULT_RETRIEVAL_HYBRID_ENABLED
    rerank_enabled: bool = DEFAULT_RETRIEVAL_RERANK_ENABLED
    candidate_pool: int = DEFAULT_RETRIEVAL_CANDIDATE_POOL
    rrf_k: int = DEFAULT_RETRIEVAL_RRF_K

    @classmethod
    def from_env(cls) -> "RetrievalSettings":
        return cls(
            hybrid_enabled=_env_bool("RETRIEVAL_HYBRID_ENABLED", DEFAULT_RETRIEVAL_HYBRID_ENABLED),
            rerank_enabled=_env_bool("RETRIEVAL_RERANK_ENABLED", DEFAULT_RETRIEVAL_RERANK_ENABLED),
            candidate_pool=int(
                os.getenv("RETRIEVAL_CANDIDATE_POOL") or DEFAULT_RETRIEVAL_CANDIDATE_POOL
            ),
            rrf_k=int(os.getenv("RETRIEVAL_RRF_K") or DEFAULT_RETRIEVAL_RRF_K),
        )


@dataclass(frozen=True)
class RerankerSettings:
    """Cross-encoder reranker config (Phase 6)."""

    model: str = DEFAULT_RERANKER_MODEL
    device: str | None = None

    @classmethod
    def from_env(cls) -> "RerankerSettings":
        return cls(
            model=os.getenv("RERANKER_MODEL") or DEFAULT_RERANKER_MODEL,
            device=os.getenv("RERANKER_DEVICE") or None,
        )
