"""Shared exception hierarchy: callers catch ``ProviderError``, never SDK types."""


class ProviderError(Exception):
    """Base for every provider failure; the cause is kept on ``.cause`` and chained."""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class ConfigurationError(ProviderError):
    """Raised when required configuration is missing or invalid."""


class LLMProviderError(ProviderError):
    """Raised when a chat/completion call fails."""


class LLMRateLimitError(LLMProviderError):
    """The LLM endpoint refused for quota (429).

    Separate from ``LLMProviderError`` because a quota is retried only after the
    server's window (``retry_after`` seconds, or ``None``); retrying sooner burns
    the same budget. Subclasses it so existing handlers still catch it.
    """

    def __init__(
        self,
        message: str,
        *,
        cause: Exception | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, cause=cause)
        self.retry_after = retry_after


class EmbeddingProviderError(ProviderError):
    """Raised when an embedding call fails."""


class SourceError(ProviderError):
    """Raised when an external content source (Notion, Drive, ...) fails."""


class WebSearchError(ProviderError):
    """Raised when a web-search tool call fails or times out."""


class EncryptionError(ProviderError):
    """Raised when encrypting/decrypting stored credentials fails."""


class PrivacyError(ProviderError):
    """Raised when identifiers could not be masked, so the text must not be sent."""


class OAuthError(ProviderError):
    """Raised when an OAuth authorize/exchange/refresh call fails."""


class OAuthReauthRequiredError(OAuthError):
    """A token refresh failed for good (e.g. Google ``invalid_grant``).

    Callers show "reconnect this provider" and do not retry: only a new consent fixes it.
    """


class AuthError(ProviderError):
    """Raised when session/magic-link authentication fails or is invalid."""


class NotFoundError(ProviderError):
    """Raised when a referenced resource (by id/email/etc.) does not exist
    in the scope the caller is allowed to see."""


class ActionError(ProviderError):
    """An Act-mode write cannot be proposed or run.

    Shown to the person; never retried, because a retried write can duplicate.
    """


class ActionRejected(ActionError):
    """The proposal itself is not allowed (bad target, blocked exposure, off)."""


class ActionExpired(ActionError):
    """The approval came after the card's time ran out."""


class ActionConflict(ActionError):
    """The approval does not match the stored proposal: already decided, a
    different payload than the one shown, or a missing acknowledgement."""


class ActionOutcomeUnknown(ActionError):
    """The write was sent and no answer came back: it may or may not exist."""


class RateLimiterError(ProviderError):
    """The rate-limit store could not be reached or answered badly.

    Callers let the request through: a limiter fault must not take the product down.
    """
