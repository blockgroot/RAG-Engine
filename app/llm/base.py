"""The LLM contract the rest of the app depends on.

Everything downstream talks to this interface, never to a concrete provider, so
implementations can be swapped freely (see ``factory.build_llm_provider``).

Most of the app only needs ``generate(prompt) -> str``. The RAG web-search
fallback (Phase 5) also needs *tool calling*, which is an optional capability:
``generate_with_tools`` raises ``NotImplementedError`` by default and is
implemented by providers that support it (``OpenAICompatProvider``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolCall:
    """A single tool/function call the model asked to make."""

    id: str
    name: str
    arguments: str  # raw JSON string as returned by the model


@dataclass(frozen=True)
class ChatResult:
    """The result of a (possibly tool-enabled) chat call.

    - ``text``         the assistant's text reply, or ``None`` if it only made
      tool calls.
    - ``tool_calls``   any tool calls the model requested (empty if none).
    - ``raw_message``  the assistant message as a plain dict, so the caller can
      append it verbatim to the message list for a follow-up (tool-result) turn.
    """

    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict = field(default_factory=dict)


class LLMProvider(ABC):
    """Abstract chat/completion provider."""

    @abstractmethod
    def generate(
        self, prompt: str, *, max_tokens: int | None = None,
        temperature: float | None = None, json_mode: bool = False,
    ) -> str:
        """Send a single prompt and return the model's text reply.

        ``max_tokens``, when set, caps the completion length (latency knob for
        slow endpoints). ``temperature`` and ``json_mode`` are optional;
        callers that need a stable JSON object (the chart classifier) set
        them. Implementations must raise ``core.exceptions.LLMProviderError``
        on failure.
        """
        raise NotImplementedError

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        """Run a chat turn that may request tool calls (optional capability).

        Default implementation raises ``NotImplementedError``; providers that
        support function calling override this. Must raise
        ``core.exceptions.LLMProviderError`` on failure.
        """
        raise NotImplementedError("this LLM provider does not support tool calling")


def call_generate(
    llm, prompt: str, *, max_tokens: int | None = None,
    temperature: float | None = None, json_mode: bool = False,
) -> str:
    """Call ``generate`` with the optional knobs, and without them if this
    provider's signature predates them (test fakes)."""
    try:
        return llm.generate(
            prompt, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
        )
    except TypeError:
        return llm.generate(prompt, max_tokens=max_tokens)
