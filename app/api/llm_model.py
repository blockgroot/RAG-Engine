"""Admin routes for the org's own model (the "Model" tab).

Admin-only via ``require_admin``; ``org_id`` comes from the signed session, never
the body, like every other tenant route (CLAUDE.md §3).

The API key is write-only across this whole module: ``PUT`` accepts one, no
route returns one, and the display path calls ``get_org_model_summary`` — which
cannot decrypt — rather than filtering a fuller object down.
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
from .deps import SessionClaims, require_admin
from .validation import bounded

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/llm-model", tags=["admin"])

MAX_MODEL_CHARS = 200
MAX_KEY_CHARS = 400

#: One short probe call, not the four-check admission suite in
#: ``scripts/verify_models.py``. This answers the question an admin actually has
#: — "did I type the key and the model id right?" — which by this project's own
#: history is the predicted failure (CLAUDE.md §5: 5 of 5 OpenRouter and 2 of 3
#: Groq model ids guessed from documentation were dead on first probe).
#:
#: 45s, not the 10s this shipped with. The worry that produced 10s was a
#: ~12-minute worst case, but that arithmetic was for FOUR sequential calls at a
#: 60s timeout with the SDK's 2 retries. This is ONE call with ``max_retries=0``,
#: so the wall clock is hard-bounded at this number — and 10s was simply below
#: what a large model on a cold NIM/vLLM worker takes to answer at all, failing
#: models that work perfectly well in chat.
PROBE_TIMEOUT = 45.0
PROBE_PROMPT = "Reply with the single word: ready"


def _probe(preset: org_model.Preset, model: str, api_key: str) -> None:
    """Call the endpoint once. Raises ``HTTPException`` with a usable message.

    The provider's own error text is passed through deliberately. An admin
    debugging a credential is exactly the person who needs to tell 401 (wrong
    key) from 404 (wrong model id) from a connection failure — three different
    next actions that a generic "couldn't connect" collapses into one dead end.
    """
    client = OpenAICompatProvider(
        model=model,
        api_key=api_key,
        base_url=preset.base_url,
        timeout=PROBE_TIMEOUT,
        max_retries=0,
    )
    # Production's own answer cap, NOT a token or two. CLAUDE.md §5 records the
    # exact trap: a reasoning model spends the whole budget on internal
    # reasoning and returns EMPTY content with finish_reason="length". Probing
    # with a tiny cap would fail such a model here even though chat gives it
    # room — and probing with an unbounded cap would pass one that then returns
    # empty in production. Only the real number tests the real thing.
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
    preset_id = bounded(payload.get("preset") or "", field="preset", limit=40)
    model = bounded(payload.get("model") or "", field="model", limit=MAX_MODEL_CHARS)
    api_key = bounded(payload.get("api_key") or "", field="api_key", limit=MAX_KEY_CHARS)

    try:
        preset = org_model.get_preset(preset_id)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    api_key = api_key.strip()
    model = model.strip()

    # Probe BEFORE saving, so a typo never becomes a broken option in every
    # member's dropdown.
    _probe(preset, model, api_key)

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
        },
    )
    return {"model": org_model.get_org_model_summary(session.org_id)}


@router.delete("", status_code=204)
def delete_model(session: SessionClaims = Depends(require_admin)):
    """Remove the model. Members fall back to the built-in list immediately."""
    org_model.delete_org_model(session.org_id)
    return None
