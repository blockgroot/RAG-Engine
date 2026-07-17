"""Provider-agnostic LLM access.

Public API:
    from app.llm import build_llm_provider
    llm = build_llm_provider()
    reply = llm.generate("hello")
"""

from .base import LLMProvider
from .openai_provider import OpenAICompatProvider
from .factory import build_llm_provider

__all__ = ["LLMProvider", "OpenAICompatProvider", "build_llm_provider"]
