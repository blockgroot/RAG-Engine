"""Single construction point for the LLM provider.

Callers do ``build_llm_provider()`` and get back something satisfying the
``LLMProvider`` interface, wired from configuration. Swapping the backend later
(e.g. to a LiteLLM-based impl for native features) means editing only this file.
"""

from __future__ import annotations

from ..config.settings import LLMSettings
from . import adapters
from .base import LLMProvider
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
        adapters.build_provider(main_endpoint(settings), timeout=settings.timeout)
    )


def main_endpoint(settings: LLMSettings) -> adapters.Endpoint:
    """The main model's endpoint, resolved through ``LLM_ADAPTER``."""
    return adapters.resolve(
        settings.adapter, base_url=settings.base_url, model=settings.model,
        api_key=settings.api_key,
    )


def aux_endpoint(settings: LLMSettings) -> adapters.Endpoint:
    """The background model's endpoint: its own when fully configured, else
    the main one with ``LLM_AUX_MODEL`` swapped in."""
    model = settings.aux_model or settings.model
    if settings.aux_has_own_endpoint:
        return adapters.resolve(
            settings.aux_adapter, base_url=settings.aux_base_url, model=model,
            api_key=settings.aux_api_key, env_prefix="LLM_AUX",
        )
    main = main_endpoint(settings)
    return adapters.Endpoint(
        adapter=main.adapter, base_url=main.base_url, model=model,
        api_key=main.api_key, kind=main.kind,
    )


def build_aux_llm_provider(settings: LLMSettings | None = None) -> LLMProvider:
    """Build the auxiliary LLM for cheap classification-style stages (Phase 19).

    Deliberately NOT wrapped for per-request model routing: that absence is
    what makes ingest contextualization unroutable, so chunks authored by
    different models are never ranked against each other inside one index
    (CLAUDE.md §3). ``test_model_selection`` asserts it by reading this
    function's source, so do not name the routing wrapper here even in prose.

    Uses its own endpoint (``LLM_AUX_ADAPTER`` or ``LLM_AUX_BASE_URL``, plus
    ``LLM_AUX_API_KEY``) when BOTH halves are set, so
    background work can draw from its own rate limit. Unset — the default —
    falls back to the main endpoint, byte-identical to the behaviour before
    those settings existed. Falling back per-field would be worse than not
    supporting this at all: a foreign base_url with the main key 401s on every
    contextualization and degrades *silently* to un-prefixed chunks, which is
    exactly the class of failure this codebase keeps paying for.
    """
    settings = settings or LLMSettings.from_env()
    return adapters.build_provider(aux_endpoint(settings), timeout=settings.timeout)
