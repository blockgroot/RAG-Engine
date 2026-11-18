"""A person's own model: their key, their bill, no Handbook limits.

The company's model (``org_model``) is the admin's; this is one per person,
offered only to them. Same presets (fixed base URLs, never a typed address),
same encryption. In a request it is selected as ``own:<model>`` so it can
never be mistaken for the company's model or one we ship.

The key is write-only: ``get_own_model_summary`` cannot decrypt, and no
route returns a key.
"""

from __future__ import annotations

from ..db.connection import get_connection
from ..security.crypto import decrypt, encrypt
from .org_model import OrgModel, get_preset, _PRESETS_BY_ID

#: The picker id prefix for a person's own model.
OWN_PREFIX = "own:"


def own_id(model: str) -> str:
    return OWN_PREFIX + model


def get_own_model(org_id: str, user_id: str) -> OrgModel | None:
    """This person's model with the key decrypted, or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT preset, model, api_key_encrypted, extract(epoch from saved_at)::bigint "
            "FROM user_llm_models WHERE org_id = %s AND user_id = %s",
            (org_id, user_id),
        ).fetchone()
    if not row:
        return None
    preset_id, model, encrypted, saved = row
    preset = _PRESETS_BY_ID.get(preset_id)
    if preset is None:  # a preset we no longer ship: its address cannot be vouched for
        return None
    return OrgModel(model=model, api_key=decrypt(encrypted), base_url=preset.base_url,
                    preset=preset_id, label=model, version=str(saved))


def get_own_model_summary(org_id: str, user_id: str) -> dict | None:
    """What the Account page shows. Never decrypts, never returns the key."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT preset, model, key_tail, agent, saved_at "
            "FROM user_llm_models WHERE org_id = %s AND user_id = %s",
            (org_id, user_id),
        ).fetchone()
    if not row:
        return None
    preset_id, model, key_tail, agent, saved = row
    preset = _PRESETS_BY_ID.get(preset_id)
    return {
        "id": own_id(model),
        "model": model,
        "preset": preset_id,
        "preset_label": preset.label if preset else preset_id,
        "key_tail": key_tail,
        "agent": agent,
        "saved_at": saved.isoformat() if saved else None,
    }


def save_own_model(org_id: str, user_id: str, preset_id: str, model: str, api_key: str,
                   agent: str | None) -> None:
    get_preset(preset_id)  # raises on an unknown provider
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO user_llm_models (org_id, user_id, preset, model, api_key_encrypted, "
            "key_tail, agent, saved_at) VALUES (%s, %s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (org_id, user_id) DO UPDATE SET preset = EXCLUDED.preset, "
            "model = EXCLUDED.model, api_key_encrypted = EXCLUDED.api_key_encrypted, "
            "key_tail = EXCLUDED.key_tail, agent = EXCLUDED.agent, saved_at = now()",
            # Last 4 only: enough to recognise the key, useless to a reader.
            (org_id, user_id, preset_id, model, encrypt(api_key), api_key[-4:], agent),
        )


def delete_own_model(org_id: str, user_id: str) -> None:
    """Idempotent: deleting nothing is not an error."""
    with get_connection() as conn:
        conn.execute("DELETE FROM user_llm_models WHERE org_id = %s AND user_id = %s",
                     (org_id, user_id))


#: Whose key pays for a call: ours (Handbook's limits), the company's (its
#: admin's limits), or the person's own (no limits).
OURS, COMPANY, OWN = "ours", "company", "own"


def billing_of(model: str | None) -> str:
    """Whose key answers a picker selection. No database: the id says it."""
    from . import catalog

    if catalog.is_selectable(model):
        return OURS
    return OWN if model.startswith(OWN_PREFIX) else COMPANY


def selectable(org_id: str, user_id: str | None, model: str | None) -> bool:
    """A model this person may pick: one we ship, the company's, or their own.
    A client-supplied id is untrusted: anything else must never reach a call."""
    from . import catalog
    from .org_model import get_org_model_summary

    if catalog.is_selectable(model):
        return True
    if model.startswith(OWN_PREFIX):
        own = get_own_model_summary(org_id, user_id) if user_id else None
        return bool(own and own["id"] == model)
    company = get_org_model_summary(org_id)
    return bool(company and company["model"] == model)
