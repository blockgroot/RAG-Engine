"""Canonical names for LLM call stages (cost logging, main vs aux model).
Call sites import these so the sets below cannot drift from what they pass."""

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
STAGE_MEMORY_EXTRACT = "memory-extract"

# Stages on the auxiliary (cheaper) model when configured: mechanical steps,
# never the answer.
AUX_LLM_STAGES = frozenset(
    {
        STAGE_REWRITE,
        STAGE_DECOMPOSE,
        STAGE_RECOVERY_EXPAND,
        STAGE_SUMMARY_FOLD,
        STAGE_INGEST_CONTEXT,
        STAGE_TONE_CLASSIFY,
        STAGE_AUDIT,
        STAGE_MEMORY_EXTRACT,
    }
)


# Stages whose output the reader SEES: only these follow a member's model
# choice. Rewrite, web decision, tone and above all the groundedness audit
# are machinery the guarantees rest on, so they stay on the configured
# model. EMPATHY_OPENER is prose in the answer, so it uses the answer's model.
USER_FACING_LLM_STAGES = frozenset(
    {
        STAGE_GENERATE,
        STAGE_TONE_RETRY,
        STAGE_WEB_ANSWER,
        STAGE_EMPATHY_OPENER,
    }
)

# An aux stage can never also be user-facing: aux exists for mechanical steps.
assert not (AUX_LLM_STAGES & USER_FACING_LLM_STAGES)
