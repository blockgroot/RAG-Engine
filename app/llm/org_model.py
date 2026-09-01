"""An org's own model: the admin brings a key, the whole org can select it.

**Bring your own KEY, not your own endpoint.** Every preset below carries a
base URL that is a constant in this file, so an admin supplies only a provider,
a model id and an API key. That is not a UX simplification — it removes the
attack surface entirely. A free-text ``base_url`` would make the server issue
outbound requests to an admin-chosen address (``169.254.169.254`` is cloud
instance-metadata credentials; anything in RFC1918 is inside our own network),
and the mitigation is not implementable at this layer: the ``openai`` SDK sets
``follow_redirects=True`` (``_base_client.py:793``) with no seam to disable it
short of passing a custom ``http_client``, and httpx re-resolves the hostname at
connect time — so a validate-then-connect check is a DNS-rebinding race, not a
guard. Supporting a self-hosted endpoint means a validating socket-level connect
hook, and that is a deliberate future decision, not something to bolt on.

Storage is one ``oauth_connections`` row with ``provider='llm'`` — no migration.
``idx_oauth_connections_org_provider_orgwide`` is already
``UNIQUE (org_id, provider) WHERE workspace_id IS NULL``, so "one model per org"
is enforced by an index that already exists, and the API key inherits the
MultiFernet encryption and org-delete cascade every OAuth token already gets.

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

#: ``provider`` value for the row. Not a data source — see ``list_connections``,
#: which filters it out precisely because every consumer of that list assumes
#: "row implies something ingestable".
PROVIDER = "llm"


@dataclass(frozen=True)
class Preset:
    """A vendor we will talk to, with the base URL fixed by us."""

    id: str
    label: str
    base_url: str
    #: Where the admin gets a key, shown on the form — by this project's own
    #: history (CLAUDE.md §5: 5/5 and 2/3 guessed model ids were dead) the
    #: predicted failure is a wrong model id, so link the list.
    models_url: str


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
    #: Bumped whenever the admin saves. Part of the client cache key so a
    #: rotated key cannot keep being served from a client built before it.
    version: str

    # No `is_openrouter` bool: request shape is per-preset DATA, looked up in
    # `routed._PRESET_EXTRA_BODY`, because there are now two presets needing
    # different non-standard fields and a boolean does not extend to a third.


def get_org_model(org_id: str) -> OrgModel | None:
    """This org's configured model, or ``None``. Decrypts the key.

    Returns ``None`` rather than raising for "not configured": having no custom
    model is the overwhelmingly common case, not an error.
    """
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
        # A preset we no longer ship. Refusing to answer is right: the stored
        # base URL would be one we cannot vouch for.
        logger.warning("Org %s has a model on unknown preset %r", org_id, preset_id)
        return None

    return OrgModel(
        model=model,
        api_key=decrypt(encrypted),
        base_url=preset.base_url,
        preset=preset_id,
        label=label or model,
        # `updated_at` would be better, but the table has no such column and
        # adding one is a migration this feature does not otherwise need. The
        # save path rewrites created_at on upsert, which makes this move.
        version=str(created),
    )


def get_org_model_summary(org_id: str) -> dict | None:
    """What the admin page shows. **Never decrypts, never returns the key.**

    A separate function from ``get_org_model`` on purpose: the display path has
    no reason to hold a plaintext credential, so it is structurally unable to
    leak one.
    """
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
        "label": label or model,
        "preset": config.get("preset"),
        "preset_label": preset.label if preset else config.get("preset"),
        "key_tail": config.get("key_tail"),
        # Dated and past-tense on purpose. This is a snapshot from when the
        # model was saved, not a live health check — rendering it as a green
        # "Connected" badge would claim something nobody verified since.
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
