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
