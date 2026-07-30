"""Single construction point for the LLM provider.

Callers do ``build_llm_provider()`` and get back something satisfying the
``LLMProvider`` interface, wired from configuration. Swapping the backend later
(e.g. to a LiteLLM-based impl for native features) means editing only this file.
"""

from __future__ import annotations

from ..config.settings import LLMSettings
from .base import LLMProvider
from .openai_provider import OpenAICompatProvider


def build_llm_provider(settings: LLMSettings | None = None) -> LLMProvider:
    """Build the configured LLM provider (defaults to reading env vars)."""
    settings = settings or LLMSettings.from_env()
    return OpenAICompatProvider(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout,
    )


def build_aux_llm_provider(settings: LLMSettings | None = None) -> LLMProvider:
    """Build the auxiliary LLM for cheap classification-style stages (Phase 19)."""
    settings = settings or LLMSettings.from_env()
    model = settings.aux_model or settings.model
    return OpenAICompatProvider(
        model=model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout,
    )
