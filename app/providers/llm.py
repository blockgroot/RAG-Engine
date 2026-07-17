"""LLM provider wrapper around the OpenAI-compatible chat completions API."""

from __future__ import annotations

import os

from openai import OpenAI, APIError, APITimeoutError, APIConnectionError

from .exceptions import ConfigurationError, LLMProviderError

# How long (seconds) to wait on a single API call before giving up.
DEFAULT_TIMEOUT = 60.0


class LLMProvider:
    """Thin wrapper over an OpenAI-compatible chat endpoint.

    Configuration is read from constructor arguments, falling back to
    environment variables so the rest of the app never hardcodes credentials:

    - ``LLM_API_KEY``
    - ``LLM_BASE_URL``
    - ``LLM_MODEL``
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL")
        self.model = model or os.getenv("LLM_MODEL")

        missing = [
            name
            for name, value in (
                ("LLM_API_KEY", self.api_key),
                ("LLM_BASE_URL", self.base_url),
                ("LLM_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                f"Missing required LLM configuration: {', '.join(missing)}"
            )

        self.timeout = timeout
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=timeout,
        )

    def generate(self, prompt: str) -> str:
        """Send a single prompt and return the assistant's text reply.

        Raises ``LLMProviderError`` on any timeout, connection, or API failure.
        """
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
                f"Could not connect to LLM endpoint at {self.base_url}", cause=exc
            ) from exc
        except APIError as exc:
            raise LLMProviderError(f"LLM API error: {exc}", cause=exc) from exc

        if not response.choices:
            raise LLMProviderError("LLM returned no choices in the response")

        content = response.choices[0].message.content
        if content is None:
            raise LLMProviderError("LLM returned an empty message content")

        return content
