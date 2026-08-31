"""Single construction point for the LLM provider.

Callers do ``build_llm_provider()`` and get back something satisfying the
``LLMProvider`` interface, wired from configuration. Swapping the backend later
(e.g. to a LiteLLM-based impl for native features) means editing only this file.
"""

from __future__ import annotations

from ..config.settings import LLMSettings
from .base import LLMProvider
from .openai_provider import OpenAICompatProvider
from .routed import RoutedLLMProvider


def build_llm_provider(settings: LLMSettings | None = None) -> LLMProvider:
    """Build the main LLM provider, wrapped for per-request model selection.

    The wrapper is transparent when nothing is selected: it delegates straight
    to the configured default, so a deployment that never sets
    ``OPENROUTER_API_KEY`` behaves exactly as it did before this feature.

    Note this is the MAIN provider only. ``build_aux_llm_provider`` is
    deliberately left unwrapped — see ``routed`` for why ingestion must not be
    routable.
    """
    settings = settings or LLMSettings.from_env()
    return RoutedLLMProvider(
        OpenAICompatProvider(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
        )
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
