"""The LLM contract the rest of the app depends on.

Most callers need ``generate``. Tool calling is optional:
``generate_with_tools`` raises ``NotImplementedError`` unless a provider
supports it (``OpenAICompatProvider``).
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
    """The result of a (possibly tool-enabled) chat call. ``text`` is ``None``
    when the model only made tool calls; ``raw_message`` is the assistant
    message as a dict, to append for a tool-result turn."""

    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict = field(default_factory=dict)


class LLMProvider(ABC):
    """Abstract chat/completion provider."""

    @abstractmethod
    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        """Send a single prompt and return the text reply. ``max_tokens`` caps the
        completion. Raises ``core.exceptions.LLMProviderError`` on failure."""
        raise NotImplementedError

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        """Run a chat turn that may request tool calls (optional capability).
        Raises ``LLMProviderError`` on failure."""
        raise NotImplementedError("this LLM provider does not support tool calling")
