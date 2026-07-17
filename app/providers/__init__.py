"""Provider abstraction layer.

Wraps the official ``openai`` client so the rest of the application talks to
LLM and embedding endpoints through a small, stable interface instead of
provider-specific details.
"""

from .llm import LLMProvider
from .embeddings import EmbeddingProvider
from .exceptions import (
    ProviderError,
    ConfigurationError,
    LLMProviderError,
    EmbeddingProviderError,
)

__all__ = [
    "LLMProvider",
    "EmbeddingProvider",
    "ProviderError",
    "ConfigurationError",
    "LLMProviderError",
    "EmbeddingProviderError",
]
