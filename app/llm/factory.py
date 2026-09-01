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
    """Build the auxiliary LLM for cheap classification-style stages (Phase 19).

    Deliberately NOT wrapped for per-request model routing: that absence is
    what makes ingest contextualization unroutable, so chunks authored by
    different models are never ranked against each other inside one index
    (CLAUDE.md §3). ``test_model_selection`` asserts it by reading this
    function's source, so do not name the routing wrapper here even in prose.

    Uses ``LLM_AUX_BASE_URL``/``LLM_AUX_API_KEY`` when BOTH are set, so
    background work can draw from its own rate limit. Unset — the default —
    falls back to the main endpoint, byte-identical to the behaviour before
    those settings existed. Falling back per-field would be worse than not
    supporting this at all: a foreign base_url with the main key 401s on every
    contextualization and degrades *silently* to un-prefixed chunks, which is
    exactly the class of failure this codebase keeps paying for.
    """
    settings = settings or LLMSettings.from_env()
    model = settings.aux_model or settings.model
    own_endpoint = settings.aux_has_own_endpoint
    return OpenAICompatProvider(
        model=model,
        api_key=settings.aux_api_key if own_endpoint else settings.api_key,
        base_url=settings.aux_base_url if own_endpoint else settings.base_url,
        timeout=settings.timeout,
    )
