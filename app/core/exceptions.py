"""Shared exception hierarchy for the whole app.

Callers only ever need to catch ``ProviderError`` (or a subclass) instead of
dealing with provider-specific error types from underlying SDKs.
"""


class ProviderError(Exception):
    """Base class for any failure raised by a provider.

    The original underlying exception (if any) is preserved on ``.cause`` and
    also chained via ``raise ... from`` so tracebacks stay useful.
    """

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class ConfigurationError(ProviderError):
    """Raised when required configuration is missing or invalid."""


class LLMProviderError(ProviderError):
    """Raised when a chat/completion call fails."""


class EmbeddingProviderError(ProviderError):
    """Raised when an embedding call fails."""


class SourceError(ProviderError):
    """Raised when an external content source (Notion, Drive, ...) fails."""


class WebSearchError(ProviderError):
    """Raised when a web-search tool call fails or times out."""
