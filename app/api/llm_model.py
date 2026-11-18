"""Admin routes for the org's own model (the "Model" tab).

The API key is write-only: ``PUT`` takes one, no route returns one, and the
display path uses ``get_org_model_summary``, which cannot decrypt.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..auth.base import OAuthTokens
from ..auth.credentials import save_connection, set_connection_config
from ..config.settings import RagSettings
from ..core.exceptions import LLMProviderError, LLMRateLimitError, ProviderError
from ..llm import org_model
from ..llm.openai_provider import OpenAICompatProvider
from ..llm.routed import preset_extra_body
from .deps import SessionClaims, require_admin
from .validation import bounded

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/llm-model", tags=["admin"])

MAX_MODEL_CHARS = 200
MAX_KEY_CHARS = 400

#: One probe call with ``max_retries=0``: "did I type the key and model id
#: right?". 30s fits a healthy answer at RAG_MAX_ANSWER_TOKENS and still fails
#: a broken one promptly. A model that hangs is a missing request field, not a
#: timeout to raise.
PROBE_TIMEOUT = 30.0
PROBE_PROMPT = "Reply with the single word: ready"


def _probe(preset: org_model.Preset, model: str, api_key: str, billing: str = "company") -> None:
    """Call the endpoint once; raise ``HTTPException`` with the provider's own text.

    401 (key), 404 (model id) and a connection failure need different fixes.
    """
    # The same per-preset request fields chat sends, including whether each may
    # be dropped on a 400, so the probe tests the production request.
    extra_body, extra_optional = preset_extra_body(preset.id, model)
    client = OpenAICompatProvider(
        model=model,
        api_key=api_key,
        base_url=preset.base_url,
        timeout=PROBE_TIMEOUT,
        max_retries=0,
        extra_body=extra_body,
        extra_body_optional=extra_optional,
        billing=billing,  # their key: never counted against our limits
    )
    # Production's answer cap: a reasoning model can spend a tiny cap and return
    # empty, and an unbounded one would pass a model that fails in production.
    max_tokens = RagSettings.from_env().max_answer_tokens

    try:
        client.generate(PROBE_PROMPT, max_tokens=max_tokens)
    except LLMRateLimitError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{preset.label} rate-limited the test ({exc}). The key and model "
                "look reachable — wait a moment and try again."
            ),
        )
    except LLMProviderError as exc:
        detail = str(exc)
        if "timed out" in detail:
            # A timeout says nothing about whether the key or the id is right,
            # so do not let it read as "rejected".
            detail = (
                f"{preset.label} did not answer within {PROBE_TIMEOUT:.0f}s. The model "
                "may be cold-starting or under load — try again. If it keeps timing "
                "out, this model is likely too slow to answer chat questions."
            )
        else:
            detail = f"{preset.label} rejected it: {detail}"
        raise HTTPException(status_code=400, detail=detail)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


#: Agent mode's test: one tool offered, one call asked for. A model that never
#: calls a tool drafts nothing, and the admin should learn that when adding it.
AGENT_TOOL = {"type": "function", "function": {
    "name": "save_note", "description": "Save a short note.",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}}
AGENT_PROMPT = 'Save a note that says "ready" with the save_note tool.'


def _agent_probe(preset: org_model.Preset, model: str, api_key: str, billing: str = "company") -> str:
    """How the model calls tools: ``"native"``, ``"text"`` (JSON text Agent mode reads), ``"none"`` or ``"unknown"`` (test failed).

    Never blocks saving: the model may still answer well.
    """
    from ..actions.loop import _calls_in_text

    extra_body, extra_optional = preset_extra_body(preset.id, model)
    client = OpenAICompatProvider(model=model, api_key=api_key, base_url=preset.base_url,
                                  timeout=PROBE_TIMEOUT, max_retries=0, extra_body=extra_body,
                                  extra_body_optional=extra_optional, billing=billing)
    try:
        result = client.generate_with_tools([{"role": "user", "content": AGENT_PROMPT}],
                                            tools=[AGENT_TOOL], tool_choice="auto")
    except ProviderError as exc:
        # A provider that refuses the tools field outright cannot run Agent mode.
        said = str(exc).lower()
        if "tool" in said and ("support" in said or "not allowed" in said or "unknown" in said):
            return "none"
        logger.warning("Agent test of %s failed: %s", model, exc)
        return "unknown"
    if any(c.name == "save_note" for c in result.tool_calls or []):
        return "native"
    return "text" if _calls_in_text(result.text, [AGENT_TOOL]) else "none"


@router.get("")
def get_model(session: SessionClaims = Depends(require_admin)):
    """The saved model, or ``{"model": null}``. Never includes the key."""
    return {"model": org_model.get_org_model_summary(session.org_id)}


@router.get("/presets")
def list_presets(session: SessionClaims = Depends(require_admin)):
    """The providers we will talk to, with their (fixed) base URLs."""
    return {
        "presets": [
            {"id": p.id, "label": p.label, "models_url": p.models_url}
            for p in org_model.PRESETS
        ]
    }


@router.put("")
def put_model(payload: dict, session: SessionClaims = Depends(require_admin)):
    """Validate, probe, then save. A model that cannot answer is never stored."""
    # Probe BEFORE saving, so a typo never becomes a broken option in every
    # member's dropdown.
    preset_id, model, api_key, agent = checked_model(payload, "company")

    from datetime import datetime, timezone

    save_connection(
        session.org_id,
        org_model.PROVIDER,
        OAuthTokens(
            access_token=api_key,
            refresh_token=None,
            expires_at=None,
            external_workspace_id=model,
            external_workspace_name=f"{model} (company)",
        ),
        connected_by_user_id=session.user_id,
    )
    set_connection_config(
        session.org_id,
        org_model.PROVIDER,
        {
            "preset": preset_id,
            # Last 4 only. Enough for an admin to recognise which key is in
            # place; useless to anyone who reads it.
            "key_tail": api_key[-4:],
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
        },
    )
    return {"model": org_model.get_org_model_summary(session.org_id)}


@router.delete("", status_code=204)
def delete_model(session: SessionClaims = Depends(require_admin)):
    """Remove the model. Members fall back to the built-in list immediately."""
    org_model.delete_org_model(session.org_id)
    return None


def checked_model(payload: dict, billing: str) -> tuple[str, str, str, str]:
    """``(preset, model, key, agent)`` from a form, after one real call with
    them. Shared by the company's model and a person's own: a model that
    cannot answer is never stored."""
    preset_id = bounded(payload.get("preset") or "", field="preset", limit=40)
    model = bounded(payload.get("model") or "", field="model", limit=MAX_MODEL_CHARS).strip()
    api_key = bounded(payload.get("api_key") or "", field="api_key", limit=MAX_KEY_CHARS).strip()
    try:
        preset = org_model.get_preset(preset_id)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _probe(preset, model, api_key, billing)
    return preset_id, model, api_key, _agent_probe(preset, model, api_key, billing)


#: The admin's limits, in tokens (input and output together); 0 = no limit.
LIMIT_FIELDS = ("person_tokens_per_minute", "person_tokens_per_day",
                "company_tokens_per_minute", "company_tokens_per_day")
#: Far above any real use, so a typo cannot overflow the column.
MAX_LIMIT_TOKENS = 1_000_000_000


def token_limit(payload: dict, name: str) -> int:
    """A whole number of tokens from 0 (no limit) to ``MAX_LIMIT_TOKENS``."""
    raw = payload.get(name, 0)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= MAX_LIMIT_TOKENS:
        raise HTTPException(status_code=400,
                            detail=f"{name} must be a whole number from 0 to {MAX_LIMIT_TOKENS:,}")
    return raw


@router.get("/limits")
def get_limits(session: SessionClaims = Depends(require_admin)):
    """The company's AI limits: Handbook's defaults until the admin sets their own."""
    from ..config.settings import RateLimitSettings
    from ..security.ai_spend import org_limits

    return {
        "limits": dict(zip(LIMIT_FIELDS, org_limits(session.org_id))),
        "defaults": dict(zip(LIMIT_FIELDS, RateLimitSettings.from_env().org_default_limits)),
    }


@router.put("/limits")
def put_limits(payload: dict, session: SessionClaims = Depends(require_admin)):
    """Save the company's AI limits. They narrow Handbook's own limits on
    Handbook's key, and are the only limits on the company's key."""
    from ..db.connection import get_connection
    from ..security.ai_spend import forget_org_limits, org_limits

    values = [token_limit(payload, name) for name in LIMIT_FIELDS]
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO org_ai_limits (org_id, person_tokens_per_minute, person_tokens_per_day, "
            "company_tokens_per_minute, company_tokens_per_day, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, now()) ON CONFLICT (org_id) DO UPDATE SET "
            "person_tokens_per_minute = EXCLUDED.person_tokens_per_minute, "
            "person_tokens_per_day = EXCLUDED.person_tokens_per_day, "
            "company_tokens_per_minute = EXCLUDED.company_tokens_per_minute, "
            "company_tokens_per_day = EXCLUDED.company_tokens_per_day, updated_at = now()",
            (session.org_id, *values),
        )
    forget_org_limits(session.org_id)
    return {"limits": dict(zip(LIMIT_FIELDS, org_limits(session.org_id)))}
