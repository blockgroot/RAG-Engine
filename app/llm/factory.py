"""Single construction point for the LLM provider."""

from __future__ import annotations

from ..config.settings import LLMSettings
from ..privacy import build_detector
from . import adapters
from .base import LLMProvider
from .masking import MaskingLLMProvider
from .routed import RoutedLLMProvider


def build_llm_provider(settings: LLMSettings | None = None) -> LLMProvider:
    """Build the main LLM provider, wrapped for per-request model selection.

    With nothing selected it delegates to the configured default. Outermost is
    identifier masking (``masking``), so no model ever sees an Aadhaar, PAN,
    card or mobile number.
    """
    settings = settings or LLMSettings.from_env()
    routed = RoutedLLMProvider(
        adapters.build_provider(
            main_endpoint(settings), timeout=settings.timeout,
            reasoning_effort=settings.reasoning_effort,
        )
    )
    return MaskingLLMProvider(routed, build_detector())


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
    """Build the auxiliary LLM for background and classification stages.

    Never wrapped for per-request model selection: ingest contextualization
    must be written by one model across a corpus. A test reads this source.

    Uses its own endpoint (``LLM_AUX_ADAPTER`` or ``LLM_AUX_BASE_URL``, plus
    ``LLM_AUX_API_KEY``) only when BOTH halves are set, so background work has
    its own rate limit; otherwise the main endpoint. Never per field: a foreign
    URL with the main key fails silently. Identifier masking still applies.
    """
    settings = settings or LLMSettings.from_env()
    return MaskingLLMProvider(
        adapters.build_provider(
            aux_endpoint(settings), timeout=settings.timeout,
            reasoning_effort=settings.reasoning_effort,
        ),
        build_detector(),
    )
