"""Structured LLM token logging (Phase 19)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from .stages import AUX_LLM_STAGES

if TYPE_CHECKING:
    from .base import LLMProvider

logger = logging.getLogger("rag.llm_usage")

__all__ = ["AUX_LLM_STAGES", "log_llm_call"]


def log_llm_call(
    stage: str,
    provider: LLMProvider,
    *,
    org_id: str | None = None,
    conversation_id: str | None = None,
    model: str | None = None,
) -> None:
    """Emit one JSON log line per LLM call for cost/usage analysis."""
    usage = getattr(provider, "last_usage", None)
    input_tokens = getattr(usage, "input_tokens", None) if usage else None
    output_tokens = getattr(usage, "output_tokens", None) if usage else None
    model_name = model or getattr(provider, "model", None)
    payload = {
        "event": "llm_call",
        "stage": stage,
        "model": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "org_id": org_id,
        "conversation_id": conversation_id,
    }
    logger.info(json.dumps(payload, default=str))
