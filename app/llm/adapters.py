"""Which provider serves the model, named in config — never implied.

``LLM_ADAPTER`` picks an entry from a FIXED list; the entry owns the base URL,
and ``LLM_MODEL`` is only a parameter passed through it. Before this, the
provider was implied by two free-text values (a URL and a model name), so a
typo or a model from the wrong vendor was accepted at boot and surfaced as
failed answers in front of users. Now an unknown adapter is a
``ConfigurationError`` at build time and ``verify_model`` lets the boot check
refuse a model the provider does not list (``scripts/check_llm_config.py``).

The vendors are ``org_model.PRESETS`` — one list, so the BYO-key form and the
deployment's own model can never disagree about a vendor's URL — plus
``custom`` for an operator-supplied OpenAI-compatible endpoint (self-hosted,
Azure-style URLs). ``custom`` is acceptable HERE and not in ``org_model``
because the URL comes from the deployment's env, set by whoever runs the
server, not from an org admin typing into a form (the SSRF surface that module
refuses).

Every adapter today speaks the OpenAI wire format, so every one builds an
``OpenAICompatProvider``. ``Adapter.kind`` is the seam for a native adapter
(Anthropic's own API, Bedrock, Vertex): add a ``kind``, a class behind
``LLMProvider``, and a branch in ``build_provider`` — nothing downstream moves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core.exceptions import ConfigurationError
from .org_model import PRESETS

logger = logging.getLogger(__name__)

#: The adapter whose base URL comes from ``LLM_BASE_URL``.
CUSTOM = "custom"
#: The only wire format implemented today.
KIND_OPENAI_COMPAT = "openai_compat"


@dataclass(frozen=True)
class Adapter:
    id: str
    label: str
    #: ``None`` only for ``custom``, whose URL is the operator's.
    base_url: str | None
    kind: str = KIND_OPENAI_COMPAT


ADAPTERS: dict[str, Adapter] = {
    p.id: Adapter(id=p.id, label=p.label, base_url=p.base_url) for p in PRESETS
}
ADAPTERS[CUSTOM] = Adapter(id=CUSTOM, label="Custom OpenAI-compatible endpoint", base_url=None)
#: Accepted spellings -> adapter id. ``gemini`` is what people type; the
#: preset id is ``google`` because that is the vendor.
ALIASES = {"gemini": "google", "claude": "anthropic", "openai_compatible": CUSTOM}


@dataclass(frozen=True)
class Endpoint:
    """What a provider is built from once the adapter has been resolved."""

    adapter: str | None  # None = legacy (no LLM_ADAPTER set)
    base_url: str | None
    model: str | None
    api_key: str | None
    kind: str = KIND_OPENAI_COMPAT


def get_adapter(name: str) -> Adapter:
    key = ALIASES.get(name, name)
    if key not in ADAPTERS:
        raise ConfigurationError(
            f"Unknown LLM adapter {name!r}. Choose one of: {', '.join(sorted(ADAPTERS))}"
        )
    return ADAPTERS[key]


def resolve(
    adapter: str | None, *, base_url: str | None, model: str | None, api_key: str | None,
    env_prefix: str = "LLM",
) -> Endpoint:
    """Turn env values into an endpoint, refusing anything ambiguous.

    * no adapter -> legacy: ``base_url`` exactly as configured (unchanged
      behaviour for every deploy that predates ``LLM_ADAPTER``);
    * a named vendor -> ITS base URL. A different ``*_BASE_URL`` alongside it
      is an error, not a silent override: two sources for one fact is how a
      Groq key ends up posted to Google;
    * ``custom`` -> ``*_BASE_URL`` is required.
    """
    if adapter is None:
        return Endpoint(adapter=None, base_url=base_url, model=model, api_key=api_key)
    spec = get_adapter(adapter)
    if spec.base_url is None:
        if not base_url:
            raise ConfigurationError(
                f"{env_prefix}_ADAPTER={CUSTOM} needs {env_prefix}_BASE_URL"
            )
        url = base_url
    else:
        if base_url and base_url.rstrip("/") != spec.base_url.rstrip("/"):
            raise ConfigurationError(
                f"{env_prefix}_BASE_URL ({base_url}) conflicts with "
                f"{env_prefix}_ADAPTER={spec.id} ({spec.base_url}). Remove "
                f"{env_prefix}_BASE_URL, or use {env_prefix}_ADAPTER={CUSTOM}."
            )
        url = spec.base_url
    return Endpoint(adapter=spec.id, base_url=url, model=model, api_key=api_key, kind=spec.kind)


def build_provider(endpoint: Endpoint, *, timeout: float):
    """The ``LLMProvider`` for an endpoint. One branch per ``kind``."""
    from .openai_provider import OpenAICompatProvider

    if endpoint.kind == KIND_OPENAI_COMPAT:
        return OpenAICompatProvider(
            model=endpoint.model,
            api_key=endpoint.api_key,
            base_url=endpoint.base_url,
            timeout=timeout,
        )
    raise ConfigurationError(f"No provider implemented for adapter kind {endpoint.kind!r}")


# -- model check -----------------------------------------------------------------

#: Outcomes of ``verify_model``. NOT_FOUND and BAD_KEY are proof of a bad
#: config; UNVERIFIED never is.
OK = "ok"
NOT_FOUND = "not_found"
BAD_KEY = "bad_key"
UNVERIFIED = "unverified"


@dataclass(frozen=True)
class ModelCheck:
    status: str
    detail: str = ""


def _normalize(model_id: str) -> str:
    # Gemini's OpenAI layer lists ids as ``models/gemini-…`` but accepts the
    # bare name in requests.
    return model_id.removeprefix("models/").strip()


def verify_model(endpoint: Endpoint, *, timeout: float = 10.0, client=None) -> ModelCheck:
    """Does the provider actually list ``endpoint.model``?

    ``NOT_FOUND`` only when the list was fetched AND the model is absent.
    Anything else — the endpoint has no ``/models``, the network is down, the
    list is empty — is ``UNVERIFIED``: a boot check that fails a deploy
    because a provider was briefly unreachable would be a new outage, not a
    safety net.
    """
    if not endpoint.model:
        return ModelCheck(NOT_FOUND, "no model configured (set LLM_MODEL)")
    if endpoint.kind != KIND_OPENAI_COMPAT:
        return ModelCheck(UNVERIFIED, f"no model listing for kind {endpoint.kind}")
    try:
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                api_key=endpoint.api_key or "missing",
                base_url=endpoint.base_url,
                timeout=timeout,
                max_retries=0,
            )
        listed = {_normalize(m.id) for m in client.models.list()}
    except Exception as exc:  # noqa: BLE001 — any failure means "could not check"
        if getattr(exc, "status_code", None) == 401:
            # A 401 is the provider saying the key is wrong — proof, unlike a
            # timeout. (Gemini answers a bad key with a 400, which stays
            # unverified: a 400 also means "this endpoint has no /models".)
            return ModelCheck(BAD_KEY, f"the provider rejected the API key (401): {exc}")
        return ModelCheck(UNVERIFIED, f"could not list models: {type(exc).__name__}: {exc}")
    if not listed:
        return ModelCheck(UNVERIFIED, "the provider returned an empty model list")
    if _normalize(endpoint.model) in listed:
        return ModelCheck(OK)
    near = sorted(m for m in listed if _normalize(endpoint.model).split("-")[0] in m)[:5]
    hint = f" Similar: {', '.join(near)}" if near else ""
    return ModelCheck(
        NOT_FOUND,
        f"model {endpoint.model!r} is not offered by adapter "
        f"{endpoint.adapter or endpoint.base_url or 'default'}.{hint}",
    )
