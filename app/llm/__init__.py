"""Provider-agnostic LLM access.

Public API:
    from app.llm import build_llm_provider
    llm = build_llm_provider()
    reply = llm.generate("hello")
"""

from .base import LLMProvider, ChatResult, ToolCall
from .openai_provider import OpenAICompatProvider
from .factory import build_aux_llm_provider, build_llm_provider
from .routed import RoutedLLMProvider, selected_model, use_model

__all__ = [
    "LLMProvider",
    "ChatResult",
    "ToolCall",
    "OpenAICompatProvider",
    "build_llm_provider",
    "build_aux_llm_provider",
    "RoutedLLMProvider",
    "use_model",
    "selected_model",
]
