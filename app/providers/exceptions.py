"""Custom exceptions for the provider abstraction layer.

The rest of the application should only ever have to catch ``ProviderError``
(or one of its subclasses) instead of dealing with provider-specific error
types from the underlying ``openai`` client.
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
    """Raised when required configuration (api_key, base_url, model) is missing."""


class LLMProviderError(ProviderError):
    """Raised when a chat/completion call fails."""


class EmbeddingProviderError(ProviderError):
    """Raised when an embedding call fails."""
