"""Canonical names for LLM call stages (Phase 19 cost logging + aux routing).

Every ``RagPipeline``/``contextualize.py`` call site that names a stage (for
``log_llm_call`` and for picking the main vs. aux model) imports the constant
here instead of retyping the string, so the aux-routing set below can never
silently drift from what a call site actually passes.
"""

from __future__ import annotations

STAGE_REWRITE = "rewrite"
STAGE_DECOMPOSE = "decompose"
STAGE_RECOVERY_EXPAND = "recovery-expand"
STAGE_SUMMARY_FOLD = "summary-fold"
STAGE_INGEST_CONTEXT = "ingest-context"
STAGE_TONE_CLASSIFY = "tone-classify"
STAGE_EMPATHY_OPENER = "empathy-opener"
STAGE_GENERATE = "generate"
STAGE_TONE_RETRY = "tone-retry"
STAGE_WEB_DECISION = "web-decision"
STAGE_WEB_ANSWER = "web-answer"
STAGE_AUDIT = "audit"

# Stages that use the auxiliary (cheaper) model when one is configured
# (``LLM_AUX_MODEL``, Phase 19) — cheap/mechanical steps, never the final
# grounded/web answer generation.
AUX_LLM_STAGES = frozenset(
    {
        STAGE_REWRITE,
        STAGE_DECOMPOSE,
        STAGE_RECOVERY_EXPAND,
        STAGE_SUMMARY_FOLD,
        STAGE_INGEST_CONTEXT,
        STAGE_TONE_CLASSIFY,
        STAGE_AUDIT,
    }
)
