"""A person's own model: their key, their bill, only the limits they set.

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
            "SELECT preset, model, api_key_encrypted, extract(epoch from saved_at)::bigint, models "
            "FROM user_llm_models WHERE org_id = %s AND user_id = %s",
            (org_id, user_id),
        ).fetchone()
    if not row:
        return None
    preset_id, model, encrypted, saved, models = row
    preset = _PRESETS_BY_ID.get(preset_id)
    if preset is None:  # a preset we no longer ship: its address cannot be vouched for
        return None
    return OrgModel(model=model, api_key=decrypt(encrypted), base_url=preset.base_url,
                    preset=preset_id, label=model, version=str(saved),
                    models=tuple(models or [model]))


def get_own_model_summary(org_id: str, user_id: str) -> dict | None:
    """What the Account page shows. Never decrypts, never returns the key."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT preset, model, key_tail, saved_at, tokens_per_minute, tokens_per_day, "
            "models, use_everywhere "
            "FROM user_llm_models WHERE org_id = %s AND user_id = %s",
            (org_id, user_id),
        ).fetchone()
    if not row:
        return None
    preset_id, model, key_tail, saved, per_minute, per_day, models, everywhere = row
    preset = _PRESETS_BY_ID.get(preset_id)
    models = list(models or [model])
    return {
        "tokens_per_minute": per_minute,
        "tokens_per_day": per_day,
        "id": own_id(models[0]),
        "ids": [own_id(m) for m in models],
        "model": models[0],
        "models": models,
        "preset": preset_id,
        "preset_label": preset.label if preset else preset_id,
        "key_tail": key_tail,
        "use_everywhere": bool(everywhere),
        "saved_at": saved.isoformat() if saved else None,
    }


def own_in_use(org_id: str | None, user_id: str | None) -> dict | None:
    """The person's own model when "use my own key everywhere" is on, else
    None: then the company's key answers for them and theirs is unused."""
    if not (org_id and user_id):
        return None
    own = get_own_model_summary(org_id, user_id)
    return own if own and own["use_everywhere"] else None


def set_use_everywhere(org_id: str, user_id: str, on: bool) -> bool:
    """The toggle. False when there is no own model to switch."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE user_llm_models SET use_everywhere = %s WHERE org_id = %s AND user_id = %s",
            (on, org_id, user_id),
        )
    return bool(cur.rowcount)


def save_own_model(org_id: str, user_id: str, preset_id: str, models: list[str], api_key: str,
                   tokens_per_minute: int = 0, tokens_per_day: int = 0) -> None:
    """Save the key and the models it offers. Saving turns "use everywhere" on:
    a key is added to be used; the toggle turns it off."""
    get_preset(preset_id)  # raises on an unknown provider
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO user_llm_models (org_id, user_id, preset, model, models, api_key_encrypted, "
            "key_tail, tokens_per_minute, tokens_per_day, use_everywhere, saved_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, now()) "
            "ON CONFLICT (org_id, user_id) DO UPDATE SET preset = EXCLUDED.preset, "
            "model = EXCLUDED.model, models = EXCLUDED.models, "
            "api_key_encrypted = EXCLUDED.api_key_encrypted, key_tail = EXCLUDED.key_tail, "
            "agent = NULL, tokens_per_minute = EXCLUDED.tokens_per_minute, "
            "tokens_per_day = EXCLUDED.tokens_per_day, use_everywhere = true, saved_at = now()",
            # Last 4 only: enough to recognise the key, useless to a reader.
            (org_id, user_id, preset_id, models[0], models, encrypt(api_key), api_key[-4:],
             tokens_per_minute, tokens_per_day),
        )
    forget_own_limits(user_id)


def save_own_limits(org_id: str, user_id: str, tokens_per_minute: int, tokens_per_day: int) -> bool:
    """Change only the limits, without the key again. False when there is no model."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE user_llm_models SET tokens_per_minute = %s, tokens_per_day = %s "
            "WHERE org_id = %s AND user_id = %s",
            (tokens_per_minute, tokens_per_day, org_id, user_id),
        )
    forget_own_limits(user_id)
    return bool(cur.rowcount)


#: Read on every call on the person's key; cached a minute like the admin's.
_OWN_LIMITS_TTL_SECONDS = 60
_own_limits: dict[str, tuple[float, tuple[int, int]]] = {}


def own_limits(org_id: str, user_id: str) -> tuple[int, int]:
    """``(tokens a minute, tokens a day)`` the person set on their own key; 0 = none.
    A read fault means none: the limiter never takes the product down."""
    import logging
    import time

    now = time.monotonic()
    cached = _own_limits.get(user_id)
    if cached and now - cached[0] < _OWN_LIMITS_TTL_SECONDS:
        return cached[1]
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT tokens_per_minute, tokens_per_day FROM user_llm_models "
                "WHERE org_id = %s AND user_id = %s",
                (org_id, user_id),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("Could not read own-model limits: %s", exc)
        return (0, 0)
    limits = (int(row[0]), int(row[1])) if row else (0, 0)
    _own_limits[user_id] = (now, limits)
    return limits


def forget_own_limits(user_id: str) -> None:
    _own_limits.pop(user_id, None)


def delete_own_model(org_id: str, user_id: str) -> None:
    """Idempotent: deleting nothing is not an error."""
    with get_connection() as conn:
        conn.execute("DELETE FROM user_llm_models WHERE org_id = %s AND user_id = %s",
                     (org_id, user_id))


#: Whose key pays for a call: ours (Handbook's limits), the company's (its
#: admin's limits), or the person's own (only the limits they set).
OURS, COMPANY, OWN = "ours", "company", "own"


def billing_of(model: str | None) -> str:
    """Whose key answers a picker selection. No database: the id says it."""
    from . import catalog

    if catalog.is_selectable(model):
        return OURS
    return OWN if model.startswith(OWN_PREFIX) else COMPANY


def selectable(org_id: str, user_id: str | None, model: str | None) -> bool:
    """A model this person may pick. With their own key in use, only its
    models: it is used everywhere. Otherwise one we ship (while Handbook offers
    its own) or one of the company's. A client-supplied id is untrusted:
    anything else must never reach a call."""
    from . import catalog
    from .org_model import offers
    from .routed import COMPANY_PREFIX, handbook_model_on

    if not model or model == catalog.AUTO:
        return True  # "the default": whoever's key pays (``routed.payer_choice``)
    mine = own_in_use(org_id, user_id)
    if mine is not None:
        return model in mine["ids"]
    if model.startswith(OWN_PREFIX):
        return False  # their own key is switched off: the company's answers
    if catalog.get(model) is not None:
        return handbook_model_on()  # Handbook's own models only while it offers them
    return offers(org_id, model.removeprefix(COMPANY_PREFIX))
