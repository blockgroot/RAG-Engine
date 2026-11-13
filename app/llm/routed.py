"""Per-request model selection, without rebuilding anything expensive.

Agents are process-wide singletons holding the embedder and reranker, so
the model can never be a constructor argument: it is read per call from a
ContextVar set once at the request boundary. The wrapper chooses between
CLIENTS (the default endpoint vs a selected backend), and satisfies
``LLMProvider`` so nothing downstream knows selection exists.

``# ponytail: request-scoped ContextVar. If two DIFFERENT models are ever
needed within one request beyond the existing main/aux split, thread it
explicitly instead of widening this.``

``build_aux_llm_provider`` does not wrap: ingest contextualization must be
written by one consistent model across a corpus.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar

from ..config.settings import (
    ClaudeSettings, GeminiSettings, GroqSettings, OllamaSettings, OpenRouterSettings,
)
from ..core.exceptions import ConfigurationError
from .base import ChatResult, LLMProvider
from . import catalog
from .catalog import (
    BACKEND_CLAUDE, BACKEND_GEMINI, BACKEND_GROQ, BACKEND_OLLAMA, BACKEND_OPENROUTER, normalize,
)
from .openai_provider import OpenAICompatProvider

logger = logging.getLogger(__name__)

_SELECTED: ContextVar[str | None] = ContextVar("llm_selected_model", default=None)
#: What the endpoint said answered, recorded at dispatch: the API layer has
#: no handle on the singleton agent's provider.
_RESOLVED: ContextVar[str | None] = ContextVar("llm_resolved_model", default=None)
#: Whose credentials answer when the selection is the org's OWN model. Kept
#: apart from ``_SELECTED`` so that stays a plain string (it reaches the
#: browser in the ``done`` event; an org_id must not).
_ORG: ContextVar[str | None] = ContextVar("llm_selected_org", default=None)

# Routing preferences sent on EVERY OpenRouter request:
# - ``data_collection: "deny"``: retrieved private content must never reach
#   a provider that may train on it. Per request, not an account toggle.
# - ``require_parameters: true``: only providers that support ``tools``.
#   A tool-less provider would make GitHub answers all fall back.
# - ``reasoning.exclude``: a leading ``<think>`` hides the ``MODE:`` tag and
#   silently skips the audit.
_ROUTING_PREFS: dict = {
    "provider": {"data_collection": "deny", "require_parameters": True},
    "reasoning": {"exclude": True},
}


#: Non-standard request fields per BYO preset: each vendor turns reasoning
#: off differently, and it must be OFF (a leading ``<think>`` hides the
#: ``MODE:`` tag and skips the audit).
_PRESET_EXTRA_BODY: dict[str, dict] = {
    # OpenRouter: no training on prompts, tool support asserted server-side.
    "openrouter": _ROUTING_PREFS,
    # NVIDIA NIM: DeepSeek v4 reasoning models hang without
    # `chat_template_kwargs`; `thinking: False` fixes that and keeps MODE parseable.
    #
    # ponytail: shape taken from NVIDIA's reasoning-model issue reports, NOT
    # verified against a live key. If a NIM reasoning model still times out,
    # this dict is the first thing to check.
    "nvidia": {"chat_template_kwargs": {"thinking": False}},
    # Anthropic: hidden thinking fills the token cap (a 200-token chart reply
    # came back empty). A model wanting another form gets it from
    # ``_MODEL_EXTRA_BODY`` or learns it from its 400 (``_repair_extras``).
    "anthropic": {"thinking": {"type": "disabled"}},
    # Gemini thinks by default and bills it as output. "minimal" is the lowest a
    # Gemini 3 model accepts ("low" thought more).
    "google": {"reasoning_effort": "minimal"},
}

#: Presets whose ``extra_body`` may be DROPPED when the endpoint rejects it:
#: NVIDIA (one model family reads the field, the rest 400 on it), Google
#: (a non-thinking model may reject ``reasoning_effort``) and Anthropic.
#: OpenRouter is deliberately ABSENT: its extras carry the no-training
#: policy, so that request must fail instead.
_PRESET_EXTRA_OPTIONAL = frozenset({"nvidia", "google", "anthropic"})


#: Models whose thinking-off differs from their vendor's: preset, then id
#: prefix. Sonnet 5.5 needs ``between_tools``; Haiku 5.5 refuses it. Sent up
#: front so no 400 is paid after a restart; unlisted models learn from the 400.
_MODEL_EXTRA_BODY: dict[str, dict[str, dict]] = {
    "anthropic": {"claude-sonnet-5-5": {"thinking": {"type": "between_tools"}}},
}


def preset_extra_body(preset_id: str, model: str | None = None) -> tuple[dict | None, bool]:
    """``(extra_body, may_be_dropped)`` for one BYO preset and model. One
    function, so the admin probe tests exactly what chat sends."""
    extra = _PRESET_EXTRA_BODY.get(preset_id)
    for prefix, override in _MODEL_EXTRA_BODY.get(preset_id, {}).items():
        if model and model.startswith(prefix):
            extra = {**extra, **override}
    return extra, preset_id in _PRESET_EXTRA_OPTIONAL


def use_model(model_id: str | None, org_id: str | None = None) -> None:
    """Select the model for the rest of this request.

    ``None``/``"auto"`` is the default. Callers validate with
    ``catalog.is_selectable`` first (this is also the reset path).
    ``org_id`` resolves an org's OWN model and must come from the signed
    session; without it a non-catalogued id fails closed.
    """
    _SELECTED.set(normalize(model_id))
    _ORG.set(org_id)
    # Reset too, so a pooled thread never reports the previous request's model.
    _RESOLVED.set(None)


@contextmanager
def default_model_only():
    """Force the configured default inside this block.

    For machinery the reader does not see (rewrite, web decision, audit),
    which must not vary with a member's pick. Restores the selection on exit,
    including on an exception.
    """
    token = _SELECTED.set(None)
    try:
        yield
    finally:
        _SELECTED.reset(token)


def selected_org() -> str | None:
    """The org whose own model was selected, if one was."""
    return _ORG.get()


def selected_model() -> str | None:
    """The model chosen for this request, or ``None`` for the default."""
    return _SELECTED.get()


def answering_model() -> str | None:
    """The model that actually answered, if any. A label, never control flow."""
    return _RESOLVED.get()


class RoutedLLMProvider(LLMProvider):
    """Dispatches each call to the default provider or a selected model,
    caching one cheap ``OpenAICompatProvider`` per selected model."""

    def __init__(
        self,
        default: LLMProvider,
        settings: OpenRouterSettings | None = None,
        groq: GroqSettings | None = None,
        ollama: OllamaSettings | None = None,
        gemini: GeminiSettings | None = None,
        claude: ClaudeSettings | None = None,
    ) -> None:
        self._default = default
        self._settings = settings or OpenRouterSettings.from_env()
        self._groq = groq or GroqSettings.from_env()
        #: Credentials per backend: every catalogued model is served by one.
        self._backends = {
            BACKEND_OPENROUTER: self._settings,
            BACKEND_GROQ: self._groq,
            BACKEND_OLLAMA: ollama or OllamaSettings.from_env(),
            BACKEND_GEMINI: gemini or GeminiSettings.from_env(),
            BACKEND_CLAUDE: claude or ClaudeSettings.from_env(),
        }
        # Keyed by (org_id, model_id, config_version), NOT model id alone: this
        # dict is process-global, and two orgs can both name `gpt-5` with their
        # own endpoints and keys. The version retires a client after key rotation.
        self._clients: dict[tuple[str | None, str, str], OpenAICompatProvider] = {}
        # Must stay ABOVE the admin probe's timeout (30s): chat's prompt is far
        # longer, so a model that passed the probe must not time out in chat.
        # Env-tunable because a slow endpoint holds a slot in the shared thread pool.
        self._custom_timeout = float(os.getenv("LLM_CUSTOM_TIMEOUT") or 45.0)

    def configured_backends(self) -> set[str]:
        """Backends with credentials. Drives which models the picker offers."""
        return {name for name, creds in self._backends.items() if creds.enabled}

    # -- dispatch ---------------------------------------------------------
    def _client_for(self, model_id: str) -> LLMProvider:
        """Build (once) the OpenRouter client for one model id.

        Falls back to the default provider when OpenRouter is not configured, so
        a stray ``model`` cannot take chat down; the label stays truthful.
        """
        org_id = selected_org()
        choice = catalog.get(model_id)

        if choice is None:
            return self._org_client(org_id, model_id)

        cache_key = (None, model_id, "catalog")
        client = self._clients.get(cache_key)
        if client is not None:
            return client

        backend = choice.backend

        creds = self._backends[backend]
        if not creds.enabled:
            logger.warning(
                "Model %s requested but the %s backend has no API key; "
                "answering with the default model instead.",
                model_id, backend,
            )
            return self._default
        api_key, base_url, timeout = creds.api_key, creds.base_url, creds.timeout
        if backend == BACKEND_CLAUDE:
            # Built like a Claude default model, so a pick and the default agree.
            from .adapters import Endpoint, build_provider

            client = build_provider(
                Endpoint(adapter="anthropic", base_url=base_url, model=model_id, api_key=api_key),
                timeout=timeout,
            )
            self._clients[cache_key] = client
            return client
        headers: dict[str, str] = {}
        if backend == BACKEND_OPENROUTER:
            # OpenRouter gets its routing extensions; a single-provider backend gets
            # only the model's own extras.
            extra_body = _ROUTING_PREFS
            if self._settings.referer:
                headers["HTTP-Referer"] = self._settings.referer
            if self._settings.title:
                headers["X-Title"] = self._settings.title
        else:
            extra_body = choice.extra_body

        client = OpenAICompatProvider(
            model=model_id,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            extra_body=extra_body,
            default_headers=headers or None,
        )
        self._clients[cache_key] = client
        return client

    def _org_client(self, org_id: str | None, model_id: str) -> LLMProvider:
        """The org's own model, or a hard failure, never the deployment's key.

        Fails CLOSED: the id came from a client and matched nothing we ship, so
        degrading would spend our shared key on an arbitrary model string.
        """
        from .org_model import get_org_model

        org = get_org_model(org_id) if org_id else None
        if org is None or org.model != model_id:
            raise ConfigurationError(
                f"Model {model_id!r} is not available for this organization."
            )

        cache_key = (org_id, model_id, org.version)
        client = self._clients.get(cache_key)
        if client is not None:
            return client

        extra_body, extra_optional = preset_extra_body(org.preset, org.model)
        client = OpenAICompatProvider(
            model=org.model,
            api_key=org.api_key,
            base_url=org.base_url,
            # Shorter than LLM_TIMEOUT: one org's slow endpoint must not hold the
            # shared thread pool every other tenant needs.
            timeout=self._custom_timeout,
            extra_body=extra_body,
            extra_body_optional=extra_optional,
        )
        self._clients[cache_key] = client
        return client

    def active(self) -> LLMProvider:
        """The provider answering right now, per this request's selection."""
        model_id = selected_model()
        if model_id is None:
            return self._default
        return self._client_for(model_id)

    # -- LLMProvider ------------------------------------------------------
    def _record(self, active: LLMProvider) -> None:
        """Publish which model answered, for the response label."""
        resolved = getattr(active, "last_resolved_model", None) or getattr(
            active, "model", None
        )
        if resolved:
            _RESOLVED.set(resolved)

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        active = self.active()
        self.last_truncated = False
        try:
            return active.generate(prompt, max_tokens=max_tokens)
        finally:
            self.last_truncated = bool(getattr(active, "last_truncated", False))
            # In `finally`, so a failed call still records which model failed.
            self._record(active)

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        active = self.active()
        try:
            return active.generate_with_tools(
                messages, tools=tools, tool_choice=tool_choice, timeout=timeout
            )
        except NotImplementedError:
            # A selected provider without tools is a configuration problem; surface it,
            # don't let it read as "the model declined".
            raise ConfigurationError(
                f"Model {getattr(active, 'model', 'unknown')} does not support "
                "tool calling."
            ) from None
        finally:
            self._record(active)

    # -- proxied diagnostics ---------------------------------------------
    # metering reads ``last_usage`` and ``model`` by getattr; proxying them to
    # the ACTIVE provider logs the model that really ran.
    @property
    def model(self) -> str | None:
        return getattr(self.active(), "model", None)

    @property
    def last_usage(self):
        return getattr(self.active(), "last_usage", None)

    @property
    def last_resolved_model(self) -> str | None:
        """What the endpoint reported as the answering model, if it said."""
        return getattr(self.active(), "last_resolved_model", None)
