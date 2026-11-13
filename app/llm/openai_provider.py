"""LLM implementation using the official OpenAI client against any
OpenAI-compatible endpoint (Anthropic, Gemini, OpenRouter, self-hosted).
Switching provider is config only: ``base_url`` + ``model`` + key.
"""

from __future__ import annotations

import logging
import re

from openai import (
    OpenAI,
    APIError,
    APITimeoutError,
    APIConnectionError,
    BadRequestError,
    RateLimitError,
)

from ..core.exceptions import (
    ConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
)
from .base import ChatResult, LLMProvider, ToolCall
from .usage import TokenUsage

logger = logging.getLogger(__name__)


def _charge(model: str, usage: TokenUsage) -> None:
    """Charge the asker for a finished call (``security/ai_spend.py``).

    Here, so no call site can forget. Before the reply is checked: a rejected
    reply was still billed.
    """
    try:
        from ..security.ai_spend import charge_call

        charge_call(model, usage.input_tokens, usage.output_tokens)
    except Exception:  # noqa: BLE001 - accounting must never fail a real call
        logger.warning("Could not charge AI spend", exc_info=True)

DEFAULT_TIMEOUT = 60.0


# Gemini puts the delay in the body, not Retry-After: read the header, then
# the body, so a quota rejection's wait is honoured.
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


#: What each (base_url, model, extras) accepts of our optional extras,
#: learned from its own 400s (``_repair_extras``), for the process.
_LEARNED_EXTRAS: dict[tuple[str | None, str, str], dict | None] = {}


def _learned_key(base_url: str | None, model: str, extras: dict | None) -> tuple[str | None, str, str]:
    import json

    return (base_url, model, json.dumps(extras, sort_keys=True, default=str))


def _value_named(message: str, field: str):
    """The JSON value an error message gives for ``field`` (``"field": {...}``),
    or None. Endpoints that refuse a setting often say what they take instead."""
    import json

    match = re.search(rf'"{re.escape(field)}"\s*:\s*', message)
    if not match:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(message[match.end():])
    except ValueError:
        return None
    return value


class OpenAICompatProvider(LLMProvider):
    """Chat provider backed by the OpenAI client. Build via
    ``factory.build_llm_provider``."""

    def __init__(
        self,
        model: str | None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        extra_body: dict | None = None,
        extra_body_optional: bool = False,
        max_retries: int | None = None,
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
        #: Non-OpenAI fields merged into every call (OpenRouter's no-training and
        #: tool-support preferences ride on EVERY request).
        self._extra_body = dict(extra_body) if extra_body else None
        #: The extras as configured, before anything a model taught us.
        self._configured_extras = self._extra_body
        if self._extra_body:
            self._extra_body = _LEARNED_EXTRAS.get(
                _learned_key(base_url, model, self._extra_body), self._extra_body)
        #: May ``extra_body`` be DROPPED if the endpoint rejects it? True only for
        #: per-vendor workarounds (NVIDIA's field 400s on most of its models).
        #: False for OpenRouter: its extras carry ``data_collection: "deny"``, so
        #: the request must FAIL rather than reach a training provider.
        self._extra_body_optional = bool(extra_body_optional)
        #: The model the endpoint said answered (differs from ``model`` behind a
        #: router): the honest label for a reader.
        self.last_resolved_model: str | None = None
        # base_url=None makes the client use OpenAI's default endpoint.
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            default_headers=default_headers or None,
            # The SDK retries twice, so `timeout` is ~3x wall clock; shared threadpool,
            # so fail fast.
            **({} if max_retries is None else {"max_retries": max_retries}),
        )

    def _with_extras(self, kwargs: dict) -> dict:
        """One request's kwargs plus ``extra_body``, as a NEW dict: ``_create``
        re-sends the original kwargs, and mutating them resent the rejected request."""
        if not self._extra_body:
            return kwargs
        return {
            **kwargs,
            "extra_body": {**self._extra_body, **kwargs.get("extra_body", {})},
        }

    def _create(self, kwargs: dict):
        """One request, retried with optional extras adjusted if the endpoint 400s.

        One place for every call (an endpoint rejects our extras everywhere).
        The repair is STICKY per process (``_LEARNED_EXTRAS``) and logged at
        warning.
        """
        try:
            return self._client.chat.completions.create(**self._with_extras(kwargs))
        except BadRequestError as exc:
            fixed = self._repair_extras(exc)
            if fixed is False:
                raise
            # Kept only if the retry works: a 400 about something else (prompt too
            # long) must not cost our fields for good.
            response = self._client.chat.completions.create(
                **({**kwargs, "extra_body": {**fixed, **kwargs.get("extra_body", {})}} if fixed else kwargs))
            logger.warning("%s / %s: our request fields %s did not work; using %s from now on.",
                           self.base_url or "the endpoint", self.model,
                           ", ".join(sorted(self._extra_body or {})), fixed or "none of them")
            self._extra_body = fixed
            _LEARNED_EXTRAS[_learned_key(self.base_url, self.model, self._configured_extras)] = fixed
            return response

    def _repair_extras(self, exc: Exception):
        """What to send instead of our OPTIONAL extras after this 400, or ``False``
        when they are required (OpenRouter's) or there are none.

        No model is named here. Per field the error names: the value it says to
        send instead, else the field dropped; naming none of ours, try without
        them. ``_create`` keeps it only if the retry works.
        """
        if not (self._extra_body and self._extra_body_optional):
            return False
        message = str(exc)
        named = [f for f in self._extra_body if re.search(rf"\b{re.escape(f)}\b", message)]
        if not named:
            return None
        fixed = dict(self._extra_body)
        for field in named:
            value = _value_named(message, field)
            if value is not None and value != fixed[field]:
                fixed[field] = value
            else:
                fixed.pop(field)
        return fixed or None

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.last_truncated = False
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if max_tokens is not None and max_tokens > 0:
            kwargs["max_tokens"] = max_tokens
        try:
            response = self._create(kwargs)
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

        # Recorded first, so the model is known even on a reply rejected as empty.
        self.last_resolved_model = getattr(response, "model", None) or self.model

        usage = response.usage
        self.last_usage = TokenUsage(
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        )
        _charge(self.model, self.last_usage)

        choice = response.choices[0]
        content = choice.message.content
        if content is None or not str(content).strip():
            # Name the model and why: a reasoning model can spend all of ``max_tokens``
            # thinking and return empty content with finish_reason="length".
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
        if getattr(choice, "finish_reason", None) == "length":
            # Non-empty but CUT at the cap: log it, nothing else says why.
            self.last_truncated = True
            logger.warning(
                "llm.truncated model=%s max_tokens=%s completion_tokens=%s -- raise "
                "RAG_MAX_ANSWER_TOKENS or use a non-reasoning model",
                self.last_resolved_model or self.model, kwargs.get("max_tokens"),
                self.last_usage.output_tokens,
            )

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
            response = self._create(kwargs)
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

        # Recorded first, so the model is known even on a reply rejected as empty.
        self.last_resolved_model = getattr(response, "model", None) or self.model

        usage = response.usage
        self.last_usage = TokenUsage(
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        )
        _charge(self.model, self.last_usage)

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
