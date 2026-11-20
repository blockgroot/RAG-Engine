"""An org's own model: the admin brings a key, the whole org can select it.

Bring your own KEY, not your own endpoint: every base URL is a constant
here. A free-text ``base_url`` is SSRF (cloud metadata, internal network),
and the SDK follows redirects and re-resolves DNS at connect time, so a
validate-then-connect check would be a race, not a guard.

Stored as one ``oauth_connections`` row with ``provider='llm'``: the
org-wide unique index gives one model per org, and the key gets the same
encryption and org-delete cascade as OAuth tokens.

``# ponytail: presets only. Custom endpoints need the socket-level check above
— add that before adding the field, not after.``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..core.exceptions import ConfigurationError
from ..db.connection import get_connection
from ..security.crypto import decrypt

logger = logging.getLogger(__name__)

#: ``provider`` value for the row. Not a data source: ``list_connections``
#: filters it out.
PROVIDER = "llm"


@dataclass(frozen=True)
class Preset:
    """A vendor we will talk to, with the base URL fixed by us."""

    id: str
    label: str
    base_url: str
    #: Where the admin gets a key and the model list (a wrong model id is the
    #: likeliest failure).
    models_url: str


# Every vendor here serves an OpenAI-compatible ``/chat/completions``. Absent
# on purpose: Azure OpenAI and self-hosted/Ollama (customer-supplied host,
# the SSRF surface refused above), Bedrock and Vertex (signed requests, not
# a bearer key).
PRESETS: tuple[Preset, ...] = (
    Preset(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        models_url="https://platform.openai.com/docs/models",
    ),
    Preset(
        id="anthropic",
        label="Anthropic",
        base_url="https://api.anthropic.com/v1",
        models_url="https://docs.anthropic.com/en/docs/about-claude/models",
    ),
    Preset(
        id="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        models_url="https://openrouter.ai/models",
    ),
    Preset(
        id="nvidia",
        label="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        models_url="https://build.nvidia.com/models",
    ),
    Preset(
        id="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        models_url="https://console.groq.com/docs/models",
    ),
    # Gemini's OpenAI-compatibility layer really ends in `/openai/`; the native
    # `/v1beta` 404s an OpenAI client.
    Preset(
        id="google",
        label="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        models_url="https://ai.google.dev/gemini-api/docs/models",
    ),
    Preset(
        id="mistral",
        label="Mistral AI",
        base_url="https://api.mistral.ai/v1",
        models_url="https://docs.mistral.ai/getting-started/models/models_overview/",
    ),
    Preset(
        id="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        models_url="https://api-docs.deepseek.com/quick_start/pricing",
    ),
    Preset(
        id="xai",
        label="xAI (Grok)",
        base_url="https://api.x.ai/v1",
        models_url="https://docs.x.ai/docs/models",
    ),
    Preset(
        id="together",
        label="Together AI",
        base_url="https://api.together.xyz/v1",
        models_url="https://docs.together.ai/docs/serverless-models",
    ),
    Preset(
        id="fireworks",
        label="Fireworks AI",
        base_url="https://api.fireworks.ai/inference/v1",
        models_url="https://fireworks.ai/models",
    ),
    Preset(
        id="cerebras",
        label="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        models_url="https://inference-docs.cerebras.ai/models/overview",
    ),
    Preset(
        id="perplexity",
        label="Perplexity",
        # No `/v1`: Perplexity serves `/chat/completions` at the domain root.
        base_url="https://api.perplexity.ai",
        models_url="https://docs.perplexity.ai/getting-started/models",
    ),
    Preset(
        id="cohere",
        label="Cohere",
        base_url="https://api.cohere.ai/compatibility/v1",
        models_url="https://docs.cohere.com/docs/models",
    ),
)

_PRESETS_BY_ID = {p.id: p for p in PRESETS}


def get_preset(preset_id: str) -> Preset:
    if preset_id not in _PRESETS_BY_ID:
        raise ConfigurationError(f"Unknown provider {preset_id!r}")
    return _PRESETS_BY_ID[preset_id]


@dataclass(frozen=True)
class OrgModel:
    """One org's configured model, with the key decrypted for use."""

    model: str
    api_key: str
    base_url: str
    preset: str
    label: str
    #: Bumped on every save; part of the client cache key so a rotated key is
    #: never served from an old client.
    version: str
    #: Every model the admin offers on this key; ``model`` is the first.
    models: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.models:  # a row saved before the list offers its one model
            object.__setattr__(self, "models", (self.model,))

    # Request shape is per-preset DATA (`routed._PRESET_EXTRA_BODY`), not a bool.


def get_org_model(org_id: str) -> OrgModel | None:
    """This org's configured model with the key decrypted, or ``None`` (the
    common case, not an error)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT external_workspace_id, external_workspace_name, "
            "access_token_encrypted, source_config, "
            "extract(epoch from created_at)::bigint "
            "FROM oauth_connections "
            "WHERE org_id = %s AND provider = %s AND workspace_id IS NULL",
            (org_id, PROVIDER),
        ).fetchone()
    if not row:
        return None

    model, label, encrypted, config, created = row
    config = config or {}
    preset_id = config.get("preset") or "openai"
    try:
        preset = get_preset(preset_id)
    except ConfigurationError:
        # A preset we no longer ship: its stored base URL cannot be vouched for.
        logger.warning("Org %s has a model on unknown preset %r", org_id, preset_id)
        return None

    return OrgModel(
        model=model,
        api_key=decrypt(encrypted),
        base_url=preset.base_url,
        preset=preset_id,
        label=label or model,
        # No `updated_at` column; the save path rewrites created_at on upsert.
        version=str(created),
        models=_models_of(model, config),
    )


def get_org_model_summary(org_id: str) -> dict | None:
    """What the admin page shows. **Never decrypts, never returns the key**, so
    the display path cannot leak one."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT external_workspace_id, external_workspace_name, "
            "source_config, created_at "
            "FROM oauth_connections "
            "WHERE org_id = %s AND provider = %s AND workspace_id IS NULL",
            (org_id, PROVIDER),
        ).fetchone()
    if not row:
        return None

    model, label, config, created = row
    config = config or {}
    preset = _PRESETS_BY_ID.get(config.get("preset") or "")
    return {
        "model": model,
        "models": list(_models_of(model, config)),
        "label": label or model,
        "preset": config.get("preset"),
        "preset_label": preset.label if preset else config.get("preset"),
        "key_tail": config.get("key_tail"),
        # Dated and past-tense: a snapshot from save time, not a live health check.
        "checked_at": config.get("checked_at"),
        "saved_at": created.isoformat() if created else None,
    }


def delete_org_model(org_id: str) -> None:
    """Remove this org's model. Idempotent — deleting nothing is not an error."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM oauth_connections "
            "WHERE org_id = %s AND provider = %s AND workspace_id IS NULL",
            (org_id, PROVIDER),
        )


def _models_of(model: str, config: dict) -> tuple[str, ...]:
    """The models offered on this key; a row saved before the list is its one model."""
    return tuple(config.get("models") or [model])


def offers(org_id: str, model: str | None) -> bool:
    """True only for one of THIS org's offered model ids."""
    if not model:
        return False
    summary = get_org_model_summary(org_id)
    return bool(summary and model in summary["models"])


class ModelListUnavailable(ConfigurationError):
    """The provider has no model list for this key: the admin types the ID."""


#: Model ids naming what is not a chat model. Format, not intent: a vendor's
#: list mixes in embedding, speech and image models nobody can chat with.
_NOT_CHAT = ("embed", "tts", "whisper", "dall-e", "transcribe", "moderation", "rerank",
             "image", "-audio", "realtime", "speech", "davinci", "babbage", "sora", "guard")
#: Far more than any provider lists; a bound, not a filter.
MAX_LISTED_MODELS = 1000
LIST_TIMEOUT = 15.0


def list_provider_models(preset: Preset, api_key: str) -> list[str]:
    """Every chat model this key can use, sorted. One request, no tokens spent:
    it also proves the key works. Raises ``ConfigurationError`` with the
    provider's reason (a refused key and an unreachable provider need
    different fixes)."""
    import httpx

    if preset.id == "anthropic":
        # Anthropic lists models on its own API, not the OpenAI-compatible one.
        url, headers = "https://api.anthropic.com/v1/models?limit=1000", {
            "x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        url, headers = preset.base_url.rstrip("/") + "/models", {"Authorization": f"Bearer {api_key}"}
    try:
        response = httpx.get(url, headers=headers, timeout=LIST_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ConfigurationError(f"Couldn't reach {preset.label}. Please try again.", cause=exc) from exc
    if response.status_code in (401, 403):
        raise ConfigurationError(f"{preset.label} refused this API key. Check it and paste it again.")
    if response.status_code >= 400:
        raise ModelListUnavailable(
            f"{preset.label} couldn't list models ({response.status_code}). "
            "Type the model ID instead.")
    try:
        body = response.json()
        rows = body.get("data") if isinstance(body, dict) else body
        ids = {str(r.get("id") or "").removeprefix("models/") for r in rows or [] if isinstance(r, dict)}
    except (ValueError, AttributeError) as exc:
        raise ModelListUnavailable(f"{preset.label} sent a model list we couldn't read. "
                                 "Type the model ID instead.", cause=exc) from exc
    chat = sorted(i for i in ids if i and not any(w in i.lower() for w in _NOT_CHAT))
    return chat[:MAX_LISTED_MODELS]
