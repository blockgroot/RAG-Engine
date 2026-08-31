"""LLM implementation using the official OpenAI Python client.

The ``openai`` client speaks the OpenAI wire format, which most providers expose
an endpoint for. Switching provider is a config change (``base_url`` + ``model``
+ key) with no code change:

    OpenAI   -> base_url unset (default),  model=gpt-5
    Gemini   -> base_url=https://generativelanguage.googleapis.com/v1beta/openai/
    Claude   -> base_url=https://api.anthropic.com/v1/
    FreeLLMAPI / self-hosted -> base_url=http://localhost:3001/v1, model=auto

Note: the Anthropic and Gemini OpenAI-compatible endpoints are provider-labelled
as beta/testing (no prompt caching, some params dropped). If we later need those
native features, this class can be swapped for a LiteLLM-backed one behind the
same ``LLMProvider`` interface — nothing downstream changes.
"""

from __future__ import annotations

import re

from openai import (
    OpenAI,
    APIError,
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
)

from ..core.exceptions import (
    ConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
)
from .base import ChatResult, LLMProvider, ToolCall
from .usage import TokenUsage

DEFAULT_TIMEOUT = 60.0

# Providers advertise the wait differently. Gemini's OpenAI-compatible endpoint
# does not set Retry-After; it puts the delay in the body ("retryDelay": "41s",
# and prose like "Please retry in 41.35s"). Read the header first, then fall
# back to the body, so a quota rejection can be honoured rather than guessed at.
_RETRY_DELAY_PATTERNS = (
    re.compile(r"'?retryDelay'?\s*:\s*'?\"?(\d+(?:\.\d+)?)s"),
    re.compile(r"retry in (\d+(?:\.\d+)?)s"),
)


def _retry_after_seconds(exc: Exception) -> float | None:
    """Best-effort extraction of how long the server wants us to wait."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass

    text = str(exc)
    for pattern in _RETRY_DELAY_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


class OpenAICompatProvider(LLMProvider):
    """Chat provider backed by the OpenAI client against any compatible endpoint.

    Prefer building this via ``factory.build_llm_provider`` so configuration
    comes from a single place.
    """

    def __init__(
        self,
        model: str | None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        extra_body: dict | None = None,
        default_headers: dict | None = None,
    ) -> None:
        if not model:
            raise ConfigurationError("Missing required LLM configuration: LLM_MODEL")
        # api_key is required by the client; most endpoints validate it.
        if not api_key:
            raise ConfigurationError("Missing required LLM configuration: LLM_API_KEY")

        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.last_usage: TokenUsage | None = None
        #: Non-OpenAI request fields merged into every call. Used for
        #: OpenRouter's routing preferences (``provider.data_collection``,
        #: ``require_parameters``) — they must ride on EVERY request rather
        #: than an account-level setting, so a training provider can never be
        #: routed to for one tenant's content.
        self._extra_body = dict(extra_body) if extra_body else None
        #: The model the endpoint said actually answered. With a router or a
        #: provider fallback this differs from ``model``, and it is the only
        #: honest thing to show a reader — "auto" is not an answer to "which
        #: model wrote this?".
        self.last_resolved_model: str | None = None
        # base_url=None makes the client use OpenAI's default endpoint.
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            default_headers=default_headers or None,
        )

    def _with_extras(self, kwargs: dict) -> dict:
        """Merge the per-provider ``extra_body`` into one request's kwargs."""
        if self._extra_body:
            kwargs["extra_body"] = {**self._extra_body, **kwargs.get("extra_body", {})}
        return kwargs

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if max_tokens is not None and max_tokens > 0:
            kwargs["max_tokens"] = max_tokens
        try:
            response = self._client.chat.completions.create(**self._with_extras(kwargs))
        except APITimeoutError as exc:
            raise LLMProviderError(
                f"LLM request timed out after {self.timeout}s", cause=exc
            ) from exc
        except APIConnectionError as exc:
            raise LLMProviderError(
                f"Could not connect to LLM endpoint at {self.base_url or 'default OpenAI'}",
                cause=exc,
            ) from exc
        except RateLimitError as exc:
            raise LLMRateLimitError(
                f"LLM rate limit / quota exceeded: {exc}",
                cause=exc,
                retry_after=_retry_after_seconds(exc),
            ) from exc
        except APIError as exc:
            raise LLMProviderError(f"LLM API error: {exc}", cause=exc) from exc

        if not response.choices:
            raise LLMProviderError("LLM returned no choices in the response")

        # Recorded before content handling so a resolved model is available
        # even on a response we go on to reject as empty.
        self.last_resolved_model = getattr(response, "model", None) or self.model

        usage = response.usage
        self.last_usage = TokenUsage(
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        )

        choice = response.choices[0]
        content = choice.message.content
        if content is None or not str(content).strip():
            # Name the model and WHY, because the bare message cost a
            # production debugging session: a reasoning model spends the whole
            # ``max_tokens`` budget on internal reasoning and returns empty
            # content with finish_reason="length". Without these fields the log
            # says only "empty content" and gives no hint which of the four
            # selectable models did it, or that the cap was the cause.
            finish = getattr(choice, "finish_reason", None)
            used = getattr(response.usage, "completion_tokens", None) if response.usage else None
            detail = f"model={self.last_resolved_model or self.model}"
            if finish:
                detail += f" finish_reason={finish}"
            if used is not None:
                detail += f" completion_tokens={used}"
            if finish == "length":
                detail += (
                    " — the token cap was consumed before any visible content "
                    "(a reasoning model spending max_tokens on reasoning). Raise "
                    "RAG_MAX_ANSWER_TOKENS or use a non-reasoning model."
                )
            raise LLMProviderError(f"LLM returned an empty message content: {detail}")

        return content

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        kwargs: dict = {"model": self.model, "messages": messages}
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            response = self._client.chat.completions.create(**self._with_extras(kwargs))
        except APITimeoutError as exc:
            raise LLMProviderError(
                f"LLM tool request timed out after {timeout or self.timeout}s", cause=exc
            ) from exc
        except APIConnectionError as exc:
            raise LLMProviderError(
                f"Could not connect to LLM endpoint at {self.base_url or 'default OpenAI'}",
                cause=exc,
            ) from exc
        except RateLimitError as exc:
            raise LLMRateLimitError(
                f"LLM rate limit / quota exceeded: {exc}",
                cause=exc,
                retry_after=_retry_after_seconds(exc),
            ) from exc
        except APIError as exc:
            raise LLMProviderError(f"LLM API error: {exc}", cause=exc) from exc

        if not response.choices:
            raise LLMProviderError("LLM returned no choices in the response")

        # Recorded before content handling so a resolved model is available
        # even on a response we go on to reject as empty.
        self.last_resolved_model = getattr(response, "model", None) or self.model

        usage = response.usage
        self.last_usage = TokenUsage(
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        )

        message = response.choices[0].message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
            for tc in (message.tool_calls or [])
        ]
        return ChatResult(
            text=message.content,
            tool_calls=tool_calls,
            raw_message=message.model_dump(),
        )
