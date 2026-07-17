"""The LLM contract the rest of the app depends on.

Everything downstream talks to this interface, never to a concrete provider, so
implementations can be swapped freely (see ``factory.build_llm_provider``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Abstract chat/completion provider."""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Send a single prompt and return the model's text reply.

        Implementations must raise ``core.exceptions.LLMProviderError`` on
        failure.
        """
        raise NotImplementedError
