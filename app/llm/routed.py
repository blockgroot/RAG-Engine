"""Per-request model selection, without rebuilding anything expensive.

**The constraint that shaped this.** Agents are ``lru_cache(maxsize=1)``
process-wide singletons (``app/api/deps.py``) because each one holds the local
BGE-M3 embedder and the cross-encoder reranker. Keying that cache by model
would load a second copy of those weights per model choice — the same mistake
as the 325MB tokenizer in CLAUDE.md §5. So the selected model can never be a
constructor argument anywhere: it has to be a value read *per call*.

**Why a wrapper rather than a flag on ``OpenAICompatProvider``.** Auto is the
deployment's own endpoint (``LLM_BASE_URL`` + ``LLM_API_KEY``, today's Gemini);
a selection is OpenRouter, with a different base_url and a different key. That
is a different client, not a different ``model`` string, so something has to
choose *between clients*. This is that thing, and it satisfies ``LLMProvider``
so nothing downstream — ``RagPipeline``, every agent, ``schedulers/runner`` —
learns that model selection exists.

**Why a ContextVar rather than a threaded parameter.** The pipeline calls the
LLM from roughly six places (grounded generation, bounded recovery, the tone
retry, the groundedness audit, decomposition, summary fold). Threading a
``model=`` through all of them would be a wide diff carrying a value that is
request-scoped by nature. Set it once at the request boundary; every stage of
that request then agrees on the model without being told.

``# ponytail: request-scoped ContextVar. If two DIFFERENT models are ever
needed within one request beyond the existing main/aux split, thread it
explicitly instead of widening this.``

**What is deliberately NOT routed.** ``build_aux_llm_provider`` does not wrap.
That is what makes ingest contextualization (``app/ingestion/pipeline.py``)
structurally unroutable: contextual chunks must be written by one consistent
model across a corpus, or chunks authored by different models end up ranked
against each other in one index. The guarantee is the absence of a wrapper,
not a rule someone has to remember.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar

from ..config.settings import GroqSettings, OpenRouterSettings
from ..core.exceptions import ConfigurationError
from .base import ChatResult, LLMProvider
from . import catalog
from .catalog import BACKEND_GROQ, BACKEND_OPENROUTER, normalize
from .openai_provider import OpenAICompatProvider

logger = logging.getLogger(__name__)

_SELECTED: ContextVar[str | None] = ContextVar("llm_selected_model", default=None)
#: What the endpoint said actually answered, recorded as the call is
#: dispatched. Kept in the request context rather than read back off a provider
#: object because the API layer has no handle on the agent's provider — agents
#: are singletons built elsewhere — and rebuilding one to ask would return a
#: fresh client that never made the call.
_RESOLVED: ContextVar[str | None] = ContextVar("llm_resolved_model", default=None)
#: Whose credentials answer, when the selection is the org's OWN model.
#:
#: A SEPARATE var rather than making ``_SELECTED`` an ``(org_id, model_id)``
#: tuple, because ``selected_model()`` is read outside this module and both
#: readers need it to stay a plain string: ``api/chat.py`` puts it in the
#: ``done`` SSE event (a tuple would ship the org_id to the browser) and
#: ``rag/query_cache.py`` folds it into the answer-cache key (a tuple would
#: silently reshape every key, catalogued models included).
_ORG: ContextVar[str | None] = ContextVar("llm_selected_org", default=None)

# Routing preferences sent on EVERY OpenRouter request.
#
# ``data_collection: "deny"`` is the load-bearing one: a RAG prompt carries
# retrieved chunks of the tenant's private Notion/Slack/Drive content, so it
# must never reach a provider that may train on or publish it. Per-request and
# enforced by OpenRouter, rather than an account-level privacy toggle — an
# account toggle would be a single global switch governing every tenant at
# once, and silently wrong the moment someone changed it in a dashboard.
#
# ``require_parameters: true`` makes capability a server-side guarantee: send
# ``tools`` and OpenRouter will not route to a provider that cannot do function
# calling. That matters because ``GitHubAgent`` grounds structurally — no tool
# call returns the fixed fallback — so a silently tool-less provider would
# answer every GitHub question with the fallback and look like a broken
# product rather than a misrouted request.
#
# ``reasoning.exclude`` keeps chain-of-thought out of ``content``. The pipeline
# parses ``MODE: A|B|C`` off the FRONT of a generation (``pipeline.py``), and a
# leading ``<think>`` block makes that regex miss — which does not fail loudly,
# it just leaves ``mode=None`` and quietly skips the groundedness audit.
_ROUTING_PREFS: dict = {
    "provider": {"data_collection": "deny", "require_parameters": True},
    "reasoning": {"exclude": True},
}


#: Non-standard request fields per BYO preset. A table, not branching: each
#: vendor's OpenAI-compatible endpoint has its own idea of how to turn reasoning
#: off, and this codebase needs it OFF on every preset — the pipeline parses
#: ``MODE: A|B|C`` off the FRONT of a generation (``rag/pipeline.py``), and a
#: leading ``<think>`` block makes that regex miss, which does not fail loudly:
#: it leaves ``mode=None`` and silently skips the groundedness audit.
_PRESET_EXTRA_BODY: dict[str, dict] = {
    # OpenRouter: keeps the tenant's retrieved private content away from
    # providers that train on prompts, and asserts tool support server-side.
    "openrouter": _ROUTING_PREFS,
    # NVIDIA NIM: its DeepSeek v4 reasoning models HANG indefinitely (not slow —
    # never return) unless the request carries `chat_template_kwargs`, because
    # the NIM template layer reads that to gate the reasoning contract. Absent
    # it the model still reasons, with no budget and no streaming contract.
    # `thinking: False` both fixes the hang and keeps the MODE tag parseable.
    #
    # ponytail: shape taken from NVIDIA's reasoning-model issue reports, NOT
    # verified against a live key. If a NIM reasoning model still times out,
    # this dict is the first thing to check.
    "nvidia": {"chat_template_kwargs": {"thinking": False}},
}


def use_model(model_id: str | None, org_id: str | None = None) -> None:
    """Select the model for the remainder of this request.

    ``None`` / ``"auto"`` means no override: the configured default answers,
    byte-identical to the behaviour before this feature existed. Callers must
    validate against ``catalog.is_selectable`` first — this does not, because
    it is also the reset path.

    ``org_id`` is what makes an org's OWN model resolvable, and it must come
    from the signed session, never a request body (CLAUDE.md §3). Without it a
    non-catalogued id cannot be resolved and the request fails closed.
    """
    _SELECTED.set(normalize(model_id))
    _ORG.set(org_id)
    # Reset in step, so a pooled thread cannot report the previous request's
    # answering model for a stream that has not generated anything yet.
    _RESOLVED.set(None)


@contextmanager
def default_model_only():
    """Force the configured default inside this block, whatever was selected.

    Used by the pipeline for every stage the reader does not see, so a member's
    dropdown choice cannot change how the machinery behaves — query rewriting,
    the web-search decision, and the groundedness audit must not vary per
    request. Restores the previous selection on exit, including on an
    exception, so one internal stage can never strand the rest of the request
    on the default model.
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
    """The model that actually answered this request, if one has yet.

    ``None`` before any generation, or when the endpoint did not name a model.
    Callers should treat it as a label, never as control flow.
    """
    return _RESOLVED.get()


class RoutedLLMProvider(LLMProvider):
    """Dispatches each call to the default provider or a selected model.

    Holds one lazily built ``OpenAICompatProvider`` per selected model. Those
    are cheap — an HTTP client and a model string, no weights — so caching them
    for the process costs nothing and avoids rebuilding a client per request.
    """

    def __init__(
        self,
        default: LLMProvider,
        settings: OpenRouterSettings | None = None,
        groq: GroqSettings | None = None,
    ) -> None:
        self._default = default
        self._settings = settings or OpenRouterSettings.from_env()
        self._groq = groq or GroqSettings.from_env()
        # Keyed by (org_id, model_id, config_version), NOT by model id alone.
        #
        # This dict is process-global: one `RoutedLLMProvider` per agent
        # singleton (`api/deps.py`, `lru_cache(maxsize=1)`), each reached by
        # every request from every tenant. Keying it by model id was sound only
        # while every id was catalogued and its credentials came from process
        # env — the id then FULLY determined the credentials. An org's own model
        # breaks that: two orgs can both name a model `gpt-5`, and the second
        # would be served the first org's cached client, sending its retrieved
        # private chunks to the first org's endpoint on the first org's key.
        # The version component additionally means a rotated key cannot keep
        # being served by a client built before the rotation.
        self._clients: dict[tuple[str | None, str, str], OpenAICompatProvider] = {}
        # Must stay ABOVE the admin probe's timeout (api/llm_model.PROBE_TIMEOUT,
        # 30s), or a model that passed the test would then fail in chat: chat
        # sends a ~2.3k-token grounded prompt against the probe's one line, so
        # its prefill is strictly slower for the same generation. 45s is that
        # 30s plus headroom, not a round number.
        #
        # Kept env-tunable because these routes run in a shared 40-thread pool:
        # a tarpit endpoint holds a slot every other tenant's chat also needs,
        # so lowering this is the lever if that ever shows up in production.
        self._custom_timeout = float(os.getenv("LLM_CUSTOM_TIMEOUT") or 45.0)

    def configured_backends(self) -> set[str]:
        """Backends with credentials. Drives which models the picker offers."""
        backends: set[str] = set()
        if self._settings.enabled:
            backends.add(BACKEND_OPENROUTER)
        if self._groq.enabled:
            backends.add(BACKEND_GROQ)
        return backends

    # -- dispatch ---------------------------------------------------------
    def _client_for(self, model_id: str) -> LLMProvider:
        """Build (once) the OpenRouter client for one model id.

        Falls back to the default provider when OpenRouter is not configured.
        Degrading is right here: the alternative is that a stray ``model`` in a
        request body takes chat down on a deployment that never enabled the
        feature. The answer is still grounded and still labelled with the model
        that actually produced it, so nothing silently misreports.
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

        headers: dict[str, str] = {}
        if backend == BACKEND_GROQ:
            if not self._groq.enabled:
                logger.warning(
                    "Model %s requested but GROQ_API_KEY is unset; answering "
                    "with the default model instead.",
                    model_id,
                )
                return self._default
            api_key, base_url = self._groq.api_key, self._groq.base_url
            timeout = self._groq.timeout
            # No routing preferences: ``provider`` and ``reasoning`` are
            # OpenRouter's own request extensions, and Groq is a single
            # provider serving its own hardware — there is nothing to route
            # between and no data policy to negotiate (Groq states it does not
            # train on inputs). Sending them would be, at best, ignored.
            extra_body = None
        else:
            if not self._settings.enabled:
                logger.warning(
                    "Model %s requested but OPENROUTER_API_KEY is unset; "
                    "answering with the default model instead.",
                    model_id,
                )
                return self._default
            api_key, base_url = self._settings.api_key, self._settings.base_url
            timeout = self._settings.timeout
            extra_body = _ROUTING_PREFS
            if self._settings.referer:
                headers["HTTP-Referer"] = self._settings.referer
            if self._settings.title:
                headers["X-Title"] = self._settings.title

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
        """The org's own model, or a hard failure — never the deployment's key.

        Fails CLOSED. The catalogued path degrades to the default provider when
        a backend is unconfigured, which is right there: the id is one we ship,
        so the worst case is answering on the wrong model. Here the id came from
        a client and matched nothing we ship, so degrading would send an
        arbitrary member-supplied model string on OUR OpenRouter key — burning
        an account-wide 50/day quota shared by every tenant (CLAUDE.md §5).
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

        client = OpenAICompatProvider(
            model=org.model,
            api_key=org.api_key,
            base_url=org.base_url,
            # Deliberately shorter than LLM_TIMEOUT. FastAPI runs these sync
            # routes in a shared 40-thread pool, so a slow tenant endpoint holds
            # a slot that every OTHER tenant's chat also needs — one org's bad
            # provider must not be able to stall the process.
            timeout=self._custom_timeout,
            extra_body=_PRESET_EXTRA_BODY.get(org.preset),
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
        try:
            return active.generate(prompt, max_tokens=max_tokens)
        finally:
            # In `finally` so a failed call still records which model failed —
            # that is exactly when someone needs to know.
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
            # A selected provider without tool support is a configuration
            # problem, not a bad request — surface it as one rather than
            # letting a caller read "no tool calls" as "the model declined".
            raise ConfigurationError(
                f"Model {getattr(active, 'model', 'unknown')} does not support "
                "tool calling."
            ) from None
        finally:
            self._record(active)

    # -- proxied diagnostics ---------------------------------------------
    #
    # metering.log_llm_call reads ``provider.last_usage`` and
    # ``provider.model`` by getattr. Proxying both to the ACTIVE provider is
    # what keeps token logging correct and — more usefully — makes it log the
    # model that really ran instead of the wrapper's idea of a default.
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
