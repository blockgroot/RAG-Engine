"""Provider abstraction layer.

Wraps the official ``openai`` client so the rest of the application talks to
LLM and embedding endpoints through a small, stable interface instead of
provider-specific details.
"""

from .llm import LLMProvider
from .embeddings import EmbeddingProvider
from .local_embeddings import LocalEmbeddingProvider
from .exceptions import (
    ProviderError,
    ConfigurationError,
    LLMProviderError,
    EmbeddingProviderError,
)

__all__ = [
    "LLMProvider",
    "EmbeddingProvider",
    "LocalEmbeddingProvider",
    "ProviderError",
    "ConfigurationError",
    "LLMProviderError",
    "EmbeddingProviderError",
]
