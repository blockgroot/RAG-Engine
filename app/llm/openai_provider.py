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

from openai import OpenAI, APIError, APITimeoutError, APIConnectionError

from ..core.exceptions import ConfigurationError, LLMProviderError
from .base import LLMProvider

DEFAULT_TIMEOUT = 60.0


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
        # base_url=None makes the client use OpenAI's default endpoint.
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def generate(self, prompt: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            )
        except APITimeoutError as exc:
            raise LLMProviderError(
                f"LLM request timed out after {self.timeout}s", cause=exc
            ) from exc
        except APIConnectionError as exc:
            raise LLMProviderError(
                f"Could not connect to LLM endpoint at {self.base_url or 'default OpenAI'}",
                cause=exc,
            ) from exc
        except APIError as exc:
            raise LLMProviderError(f"LLM API error: {exc}", cause=exc) from exc

        if not response.choices:
            raise LLMProviderError("LLM returned no choices in the response")

        content = response.choices[0].message.content
        if content is None:
            raise LLMProviderError("LLM returned an empty message content")

        return content
