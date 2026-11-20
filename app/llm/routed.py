"""Per-request model selection, without rebuilding anything expensive.

Agents are process-wide singletons holding the embedder and reranker, so
the model can never be a constructor argument: it is read per call from a
ContextVar set once at the request boundary. The wrapper chooses between
CLIENTS (the default endpoint vs a selected backend), and satisfies
``LLMProvider`` so nothing downstream knows selection exists.

``# ponytail: request-scoped ContextVar. If two DIFFERENT models are ever
needed within one request beyond the existing main/aux split, thread it
explicitly instead of widening this.``

Machinery (rewrite, audit, web decision, ...) and company work (indexing,
gap grouping, reports) never follow the picker. They run on whoever's KEY
pays (``use_payer_keys``, ``payer_keys``): inside a question the person's own
model, else the company's, else ours; company work the company's, else ours.
A key a person or admin saved is a standing choice, not a per-question pick.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar

from ..config.settings import (
    ClaudeSettings, GeminiSettings, GroqSettings, OllamaSettings, OpenRouterSettings,
)
from ..core.exceptions import ConfigurationError, NoModelConfigured
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
#: Whose OWN model (``own:<model>``) answers: the person who asked.
_USER: ContextVar[str | None] = ContextVar("llm_selected_user", default=None)
#: The key machinery and company work run on: ``own:<model>``,
#: ``company:<model>``, or None for ours (``payer_choice``).
_PAYER: ContextVar[str | None] = ContextVar("llm_payer_model", default=None)
#: Inside ``default_model_only``: the call is machinery, so it runs on ``_PAYER``.
_MACHINERY: ContextVar[bool] = ContextVar("llm_machinery", default=False)

#: The picker id prefix for the company's model (a person's is ``own:``).
COMPANY_PREFIX = "company:"

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


def use_model(model_id: str | None, org_id: str | None = None,
              user_id: str | None = None) -> None:
    """Select the model for the rest of this request.

    ``None``/``"auto"`` is the default. Callers validate with
    ``catalog.is_selectable`` first (this is also the reset path).
    ``org_id`` resolves an org's OWN model and must come from the signed
    session; without it a non-catalogued id fails closed.
    """
    _SELECTED.set(normalize(model_id))
    _ORG.set(org_id)
    _USER.set(user_id)
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
    machinery = _MACHINERY.set(True)
    try:
        yield
    finally:
        _MACHINERY.reset(machinery)
        _SELECTED.reset(token)


def payer_choice(org_id: str | None, user_id: str | None = None) -> str | None:
    """Whose key runs everything this person causes: their own model when
    "use my own key everywhere" is on, else the company's, else None (ours).
    Without a person (indexing, company jobs), the company's or ours. A read
    fault is ours: the checks must still run."""
    if not org_id:
        return None
    from .org_model import get_org_model_summary
    from .own_model import own_in_use

    try:
        own = own_in_use(org_id, user_id)
        if own:
            return own["id"]
        company = get_org_model_summary(org_id)
        return COMPANY_PREFIX + company["model"] if company else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read whose key pays for org %s: %s", org_id, exc)
        return None


def handbook_model_on() -> bool:
    """Whether Handbook answers on its own key when nobody added one
    (``HANDBOOK_MODEL_ENABLED``)."""
    from ..config.settings import LLMSettings

    return LLMSettings.from_env().handbook_model


def model_available(org_id: str | None, user_id: str | None = None) -> bool:
    """Can anything answer for this person (or, without one, the company's
    work)? Handbook's model, or a key the company or they added."""
    return handbook_model_on() or payer_choice(org_id, user_id) is not None


def use_payer_keys(org_id: str | None, user_id: str | None = None) -> str | None:
    """For the rest of this request, machinery runs on ``payer_choice``.
    Returns it, so a surface with no picker (Slack) can answer on it too."""
    choice = payer_choice(org_id, user_id)
    _PAYER.set(choice)
    if choice:
        _ORG.set(org_id)
        _USER.set(user_id)
    return choice


@contextmanager
def payer_keys(org_id: str | None, user_id: str | None = None):
    """``use_payer_keys`` for one block of company work, restored after: a
    worker thread runs many companies' jobs one after another."""
    tokens = (_PAYER.set(payer_choice(org_id, user_id)), _ORG.set(org_id), _USER.set(user_id))
    try:
        yield
    finally:
        _USER.reset(tokens[2])
        _ORG.reset(tokens[1])
        _PAYER.reset(tokens[0])


def on_company_key(fn):
    """Run a company job (it takes ``org_id``) on the company's key, else ours."""
    import functools
    import inspect

    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        org_id = signature.bind_partial(*args, **kwargs).arguments.get("org_id")
        with payer_keys(org_id):
            return fn(*args, **kwargs)

    return wrapper


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

        if model_id.startswith(COMPANY_PREFIX):
            # Prefixed so a company model named like one of ours ("claude-haiku-5-5")
            # still runs on the COMPANY's key, never ours.
            return self._org_client(org_id, model_id[len(COMPANY_PREFIX):])
        if choice is None:
            from .own_model import OWN_PREFIX

            if model_id.startswith(OWN_PREFIX):
                return self._own_client(org_id, _USER.get(), model_id[len(OWN_PREFIX):])
            return self._org_client(org_id, model_id)  # an unprefixed company id, from older pages

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
        if org is None or model_id not in org.models:
            raise ConfigurationError(
                f"Model {model_id!r} is not available for this organization."
            )

        cache_key = (org_id, COMPANY_PREFIX + model_id, org.version)
        client = self._clients.get(cache_key)
        if client is not None:
            return client

        extra_body, extra_optional = preset_extra_body(org.preset, model_id)
        client = OpenAICompatProvider(
            model=model_id,
            api_key=org.api_key,
            base_url=org.base_url,
            # Shorter than LLM_TIMEOUT: one org's slow endpoint must not hold the
            # shared thread pool every other tenant needs.
            timeout=self._custom_timeout,
            extra_body=extra_body,
            extra_body_optional=extra_optional,
            billing="company",
        )
        self._clients[cache_key] = client
        return client

    def _own_client(self, org_id: str | None, user_id: str | None, model: str) -> LLMProvider:
        """The asker's own model on their own key, or a hard failure: never ours."""
        from .own_model import get_own_model

        own = get_own_model(org_id, user_id) if org_id and user_id else None
        if own is None or model not in own.models:
            raise ConfigurationError(f"Your own model {model!r} is not set up.")
        # ponytail: one cached client per person who uses their own model; an LRU if that grows.
        cache_key = (f"user:{user_id}", model, own.version)
        client = self._clients.get(cache_key)
        if client is not None:
            return client
        extra_body, extra_optional = preset_extra_body(own.preset, model)
        client = OpenAICompatProvider(
            model=model, api_key=own.api_key, base_url=own.base_url,
            timeout=self._custom_timeout, extra_body=extra_body,
            extra_body_optional=extra_optional, billing="own",
        )
        self._clients[cache_key] = client
        return client

    def _ours(self) -> LLMProvider:
        """Handbook's own model, unless it is switched off: then nothing
        answers on our key, and the caller says "connect a model"."""
        if not handbook_model_on():
            raise NoModelConfigured("No AI model is connected for this company.")
        return self._default

    def _payer_client(self) -> LLMProvider:
        """Machinery's provider: whoever's key pays (``payer_choice``), else
        ours. A key removed since is ours, never a failed check."""
        choice = _PAYER.get()
        if not choice:
            return self._ours()
        try:
            return self._client_for(choice)
        except ConfigurationError as exc:
            logger.warning("Machinery key unavailable (%s); using the default model", exc)
            return self._ours()

    def active(self) -> LLMProvider:
        """The provider answering right now, per this request's selection."""
        if _MACHINERY.get():
            return self._payer_client()
        model_id = selected_model()
        if model_id is None:
            # "Default": their own key when it is used everywhere; else ours, or
            # with ours off, whoever's key pays.
            from .own_model import OWN_PREFIX

            if (_PAYER.get() or "").startswith(OWN_PREFIX) or not handbook_model_on():
                return self._payer_client()
            return self._default
        if catalog.get(model_id) is not None and not handbook_model_on():
            raise NoModelConfigured("Handbook's own models are not offered here.")
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


class PayerKeyProvider(RoutedLLMProvider):
    """The background model, on whoever's key pays (``payer_keys``): the
    company's for company work, the person's or company's for a question's
    machinery. Never the picker: one company's corpus is written by one model,
    its own or ours."""

    def active(self) -> LLMProvider:
        return self._payer_client()
