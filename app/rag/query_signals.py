"""Structured per-query production signals (Phase 22).

Emits one JSON log line per answered question so gate/reuse thresholds can be
validated against real traffic (top_score distribution, response modes, refusal
rate, retrieval reuse) without a separate observability stack.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline import RagResult

logger = logging.getLogger("rag.query_signals")


def log_query_signal(
    result: RagResult,
    *,
    org_id: str,
    conversation_id: str | None = None,
) -> None:
    """Emit one JSON log line summarizing retrieval and answer outcome."""
    payload = {
        "event": "query_signal",
        "org_id": org_id,
        "conversation_id": conversation_id,
        "top_score": result.top_score,
        "response_mode": result.response_mode,
        "answered": result.answered,
        "source": result.final_answer_source or result.source,
        "retrieval_reused": result.retrieval_reused,
        "cache_hit": result.cache_hit,
        "recovery_used": result.recovery_used,
        "question_decomposed": result.question_decomposed,
        "latency_ms": result.latency_ms,
    }
    logger.info(json.dumps(payload, default=str))
