"""Cross-cutting building blocks shared across the app (errors, etc.)."""

from .exceptions import (
    ProviderError,
    ConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
    EmbeddingProviderError,
)

__all__ = [
    "ProviderError",
    "ConfigurationError",
    "LLMProviderError",
    "LLMRateLimitError",
    "EmbeddingProviderError",
]
