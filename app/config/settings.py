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
