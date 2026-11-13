"""Model endpoints and the model-backed ingest and query steps."""

from __future__ import annotations

import os
from dataclasses import dataclass

from ._env import _env_positive_int, env_bool

DEFAULT_TIMEOUT = 60.0

DEFAULT_EMBEDDING_DIM = 1024

# Groq's OpenAI-compatible endpoint, a second selectable backend.
DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"


@dataclass(frozen=True)
class LLMSettings:
    """LLM provider configuration."""

    model: str | None
    aux_model: str | None
    api_key: str | None
    base_url: str | None
    timeout: float = DEFAULT_TIMEOUT
    #: Separate endpoint for the AUX (background) model. ``None`` shares the main
    #: key and base_url; setting both puts background work on its own quota.
    aux_base_url: str | None = None
    aux_api_key: str | None = None
    #: The provider serving the model, from a fixed list (``app/llm/adapters.py``),
    #: which owns the base URL. ``None`` = legacy: ``LLM_BASE_URL`` as-is, with a
    #: startup warning.
    adapter: str | None = None
    #: The same for background work; with ``LLM_AUX_API_KEY`` it replaces
    #: ``LLM_AUX_BASE_URL``.
    aux_adapter: str | None = None
    #: Boot-time config check (``scripts/check_llm_config.py``): ``strict`` refuses
    #: a provably wrong config, ``warn`` logs, ``off`` skips. An unreachable model
    #: list never fails.
    config_check: str = "strict"
    #: Sent as ``reasoning_effort`` (``low``, ``none``...) so a reasoning model does
    #: not spend a short call's cap thinking. Dropped where refused.
    reasoning_effort: str | None = None

    @property
    def aux_has_own_endpoint(self) -> bool:
        """True when background work draws from a different rate limit.

        Needs BOTH an endpoint (base_url or adapter) and a key: half-configured
        would send a credential to the wrong host, so it means "not configured".
        """
        return bool((self.aux_base_url or self.aux_adapter) and self.aux_api_key)

    @classmethod
    def from_env(cls) -> "LLMSettings":
        check = (os.getenv("LLM_CONFIG_CHECK") or "strict").strip().lower()
        return cls(
            model=os.getenv("LLM_MODEL"),
            aux_model=os.getenv("LLM_AUX_MODEL") or None,
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
            timeout=float(os.getenv("LLM_TIMEOUT") or cls.timeout),
            aux_base_url=os.getenv("LLM_AUX_BASE_URL") or None,
            aux_api_key=os.getenv("LLM_AUX_API_KEY") or None,
            adapter=(os.getenv("LLM_ADAPTER") or "").strip().lower() or None,
            aux_adapter=(os.getenv("LLM_AUX_ADAPTER") or "").strip().lower() or None,
            config_check=check if check in ("strict", "warn", "off") else "strict",
            reasoning_effort=(os.getenv("LLM_REASONING_EFFORT") or "").strip().lower() or None,
        )


@dataclass(frozen=True)
class OpenRouterSettings:
    """Credentials for user-selectable OpenRouter models.

    Apart from ``LLMSettings`` (the default): with no key here the picker is
    simply empty.
    """

    api_key: str | None
    # OpenRouter's OpenAI-compatible endpoint (Multi-Model Selection).
    base_url: str = "https://openrouter.ai/api/v1"
    timeout: float = DEFAULT_TIMEOUT
    #: Sent to OpenRouter for app attribution; optional, purely cosmetic.
    referer: str | None = None
    title: str | None = None

    @property
    def enabled(self) -> bool:
        """No key = no selectable models. The dropdown hides itself."""
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "OpenRouterSettings":
        return cls(
            api_key=os.getenv("OPENROUTER_API_KEY") or None,
            base_url=os.getenv("OPENROUTER_BASE_URL") or cls.base_url,
            timeout=float(os.getenv("OPENROUTER_TIMEOUT") or cls.timeout),
            referer=os.getenv("OPENROUTER_REFERER") or None,
            title=os.getenv("OPENROUTER_TITLE") or None,
        )


@dataclass(frozen=True)
class GroqSettings:
    """Credentials for Groq-hosted selectable models.

    Its own account and quota, so one exhausted provider does not empty the
    picker. Groq does not train on inputs, so no routing preferences are sent.
    """

    api_key: str | None
    base_url: str = DEFAULT_GROQ_BASE_URL
    timeout: float = DEFAULT_TIMEOUT

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "GroqSettings":
        return cls(
            api_key=os.getenv("GROQ_API_KEY") or None,
            base_url=os.getenv("GROQ_BASE_URL") or cls.base_url,
            timeout=float(os.getenv("GROQ_TIMEOUT") or cls.timeout),
        )


@dataclass(frozen=True)
class ClaudeSettings:
    """Credentials for the Claude models in the picker (Sonnet and Haiku).

    ``CLAUDE_API_KEY``, else ``LLM_API_KEY`` when the default is already Claude
    (``LLM_ADAPTER=anthropic``): one key, one credit.
    """

    api_key: str | None
    base_url: str = "https://api.anthropic.com/v1"
    timeout: float = DEFAULT_TIMEOUT

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "ClaudeSettings":
        adapter = (os.getenv("LLM_ADAPTER") or "").strip().lower()
        main_key = os.getenv("LLM_API_KEY") if adapter in ("anthropic", "claude") else None
        return cls(
            api_key=os.getenv("CLAUDE_API_KEY") or main_key or None,
            timeout=float(os.getenv("LLM_TIMEOUT") or cls.timeout),
        )


@dataclass(frozen=True)
class OllamaSettings:
    """Credentials for Ollama Cloud selectable models; its own account and quota.

    Reasoning is kept low or off per model (``app/llm/catalog.py``).
    """

    api_key: str | None
    base_url: str = "https://ollama.com/v1"
    timeout: float = DEFAULT_TIMEOUT

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "OllamaSettings":
        return cls(
            api_key=os.getenv("OLLAMA_API_KEY") or None,
            base_url=os.getenv("OLLAMA_BASE_URL") or cls.base_url,
            timeout=float(os.getenv("OLLAMA_TIMEOUT") or cls.timeout),
        )


@dataclass(frozen=True)
class GeminiSettings:
    """Credentials for Gemini as a selectable model, so it stays in the picker
    when the deployment's default model is another vendor's."""

    api_key: str | None
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    timeout: float = DEFAULT_TIMEOUT

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "GeminiSettings":
        return cls(
            api_key=os.getenv("GEMINI_API_KEY") or None,
            base_url=os.getenv("GEMINI_BASE_URL") or cls.base_url,
            timeout=float(os.getenv("GEMINI_TIMEOUT") or cls.timeout),
        )


@dataclass(frozen=True)
class EmbeddingSettings:
    """Embedding provider configuration."""

    backend: str
    model: str | None
    device: str | None
    api_key: str | None
    base_url: str | None
    timeout: float = DEFAULT_TIMEOUT
    batch_size: int = 16
    #: Size to ask a remote model for (EMBEDDING_SEND_DIMENSIONS=true), so a model
    #: of another natural size fits the database. Off: unsupported providers reject it.
    dimensions: int | None = None

    @classmethod
    def from_env(cls) -> "EmbeddingSettings":
        return cls(
            dimensions=(
                int(os.getenv("EMBEDDING_DIM") or DEFAULT_EMBEDDING_DIM)
                if env_bool("EMBEDDING_SEND_DIMENSIONS", False)
                else None
            ),
            backend=(os.getenv("EMBEDDING_BACKEND") or "local").lower(),
            model=os.getenv("EMBEDDING_MODEL") or "BAAI/bge-m3",
            device=os.getenv("EMBEDDING_DEVICE") or None,
            api_key=os.getenv("EMBEDDING_API_KEY"),
            base_url=os.getenv("EMBEDDING_BASE_URL"),
            timeout=float(os.getenv("EMBEDDING_TIMEOUT") or cls.timeout),
            batch_size=max(
                1, int(os.getenv("EMBED_BATCH_SIZE") or cls.batch_size)
            ),
        )


@dataclass(frozen=True)
class DecomposeSettings:
    """Compound-question decomposition before retrieval.

    A heuristic spots multi-ask questions; only then the model splits them.
    Each part is retrieved; pools are merged and reranked.
    """

    enabled: bool = True

    @classmethod
    def from_env(cls) -> "DecomposeSettings":
        return cls(
            enabled=env_bool("DECOMPOSE_ENABLED", cls.enabled),
        )


@dataclass(frozen=True)
class ContextualSettings:
    """Ingest-time contextual retrieval settings."""

    enabled: bool = True
    defer: bool = True
    concurrency: int = 2
    max_chunks: int = 200
    hypothetical_questions: bool = True

    @classmethod
    def from_env(cls) -> "ContextualSettings":
        return cls(
            enabled=env_bool("INGEST_CONTEXTUAL_ENABLED", cls.enabled),
            defer=env_bool("INGEST_CONTEXTUAL_DEFER", cls.defer),
            concurrency=_env_positive_int(
                "INGEST_CONTEXTUAL_CONCURRENCY", cls.concurrency
            ),
            max_chunks=_env_positive_int(
                "INGEST_CONTEXTUAL_MAX_CHUNKS", cls.max_chunks
            ),
            hypothetical_questions=env_bool(
                "INGEST_HYPOTHETICAL_QUESTIONS_ENABLED",
                cls.hypothetical_questions,
            ),
        )


@dataclass(frozen=True)
class KeywordExtractionSettings:
    """Keyword-extraction settings for ingest-time chunk enrichment."""

    enabled: bool = True
    top_n: int = 6

    @classmethod
    def from_env(cls) -> "KeywordExtractionSettings":
        return cls(
            enabled=env_bool("INGEST_KEYWORDS_ENABLED", cls.enabled),
            top_n=_env_positive_int("INGEST_KEYWORDS_TOP_N", cls.top_n),
        )


@dataclass(frozen=True)
class RerankerSettings:
    """Cross-encoder reranker config.

    - ``backend``: ``local`` (in-process CrossEncoder) or ``remote``
    (Jina-compatible ``/v1/rerank``) for hosts without the RAM.
    - ``api_key``/``base_url``: remote only; fall back to the embedding key and
    URL, then Jina's endpoint.
    """

    backend: str = "local"
    model: str = "BAAI/bge-reranker-v2-m3"
    device: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "RerankerSettings":
        backend = (os.getenv("RERANKER_BACKEND") or "local").lower()
        default_model = (
            "jina-reranker-v3" if backend == "remote" else cls.model
        )
        api_key = os.getenv("RERANKER_API_KEY") or os.getenv("EMBEDDING_API_KEY")
        base_url = (
            os.getenv("RERANKER_BASE_URL")
            or os.getenv("EMBEDDING_BASE_URL")
            or ("https://api.jina.ai/v1" if backend == "remote" else None)
        )
        return cls(
            backend=backend,
            model=os.getenv("RERANKER_MODEL") or default_model,
            device=os.getenv("RERANKER_DEVICE") or None,
            api_key=api_key,
            base_url=base_url,
            timeout=float(os.getenv("RERANKER_TIMEOUT") or cls.timeout),
        )


@dataclass(frozen=True)
class LLMPacingSettings:
    """How much of the LLM rate limit background work may use."""

    enabled: bool = True
    # LLM request pacing. When the aux model shares the main key, background
    # work and live questions share one rate limit. reserve_rpm is never used by
    # background work, so it stays free for people; interactive calls are never
    # throttled. Raise max_rpm for a paid tier; LLM_PACING_ENABLED=false only for
    # an endpoint with no real limit.
    max_rpm: int = 15
    reserve_rpm: int = 5
    #: How long a background call waits for a slot before degrading (a wedged
    #: ingest is worse than a chunk without its context prefix).
    max_wait_seconds: float = 45.0

    @classmethod
    def from_env(cls) -> "LLMPacingSettings":
        max_rpm = int(os.getenv("LLM_MAX_RPM") or cls.max_rpm)
        reserve = int(os.getenv("LLM_RESERVE_RPM") or cls.reserve_rpm)
        # A reserve at or above the limit would starve background work
        # completely and silently; clamp so at least one slot remains.
        reserve = max(0, min(reserve, max(0, max_rpm - 1)))
        return cls(
            enabled=env_bool("LLM_PACING_ENABLED", True),
            max_rpm=max_rpm,
            reserve_rpm=reserve,
            max_wait_seconds=float(
                os.getenv("LLM_PACING_MAX_WAIT_SECONDS")
                or cls.max_wait_seconds
            ),
        )
