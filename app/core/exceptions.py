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


class EncryptionError(ProviderError):
    """Raised when encrypting/decrypting stored credentials fails."""


class OAuthError(ProviderError):
    """Raised when an OAuth authorize/exchange/refresh call fails."""


class OAuthReauthRequiredError(OAuthError):
    """Raised when a token refresh fails terminally (e.g. Google's
    ``invalid_grant`` from a revoked/expired/rotated-out refresh token).

    Distinct from a generic ``OAuthError`` so callers (the job worker, admin
    API routes) can surface an actionable "reconnect this provider" message
    instead of a bare failure, and so they know NOT to retry — a terminal
    refresh failure will not succeed on a later attempt without the admin
    re-completing the OAuth consent flow.
    """


class AuthError(ProviderError):
    """Raised when session/magic-link authentication fails or is invalid."""


class NotFoundError(ProviderError):
    """Raised when a referenced resource (by id/email/etc.) does not exist
    in the scope the caller is allowed to see."""
