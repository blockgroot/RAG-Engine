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
from contextlib import contextmanager
from contextvars import ContextVar

from ..config.settings import OpenRouterSettings
from ..core.exceptions import ConfigurationError
from .base import ChatResult, LLMProvider
from .catalog import normalize
from .openai_provider import OpenAICompatProvider

logger = logging.getLogger(__name__)

_SELECTED: ContextVar[str | None] = ContextVar("llm_selected_model", default=None)
#: What the endpoint said actually answered, recorded as the call is
#: dispatched. Kept in the request context rather than read back off a provider
#: object because the API layer has no handle on the agent's provider — agents
#: are singletons built elsewhere — and rebuilding one to ask would return a
#: fresh client that never made the call.
_RESOLVED: ContextVar[str | None] = ContextVar("llm_resolved_model", default=None)

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


def use_model(model_id: str | None) -> None:
    """Select the model for the remainder of this request.

    ``None`` / ``"auto"`` means no override: the configured default answers,
    byte-identical to the behaviour before this feature existed. Callers must
    validate against ``catalog.is_selectable`` first — this does not, because
    it is also the reset path.
    """
    _SELECTED.set(normalize(model_id))
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
    ) -> None:
        self._default = default
        self._settings = settings or OpenRouterSettings.from_env()
        self._clients: dict[str, OpenAICompatProvider] = {}

    # -- dispatch ---------------------------------------------------------
    def _client_for(self, model_id: str) -> LLMProvider:
        """Build (once) the OpenRouter client for one model id.

        Falls back to the default provider when OpenRouter is not configured.
        Degrading is right here: the alternative is that a stray ``model`` in a
        request body takes chat down on a deployment that never enabled the
        feature. The answer is still grounded and still labelled with the model
        that actually produced it, so nothing silently misreports.
        """
        if not self._settings.enabled:
            logger.warning(
                "Model %s requested but OPENROUTER_API_KEY is unset; "
                "answering with the default model instead.",
                model_id,
            )
            return self._default

        client = self._clients.get(model_id)
        if client is None:
            headers = {}
            if self._settings.referer:
                headers["HTTP-Referer"] = self._settings.referer
            if self._settings.title:
                headers["X-Title"] = self._settings.title
            client = OpenAICompatProvider(
                model=model_id,
                api_key=self._settings.api_key,
                base_url=self._settings.base_url,
                timeout=self._settings.timeout,
                extra_body=_ROUTING_PREFS,
                default_headers=headers or None,
            )
            self._clients[model_id] = client
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
