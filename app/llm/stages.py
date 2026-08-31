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


# Stages whose output the USER ACTUALLY READS. Only these follow a member's
# model choice (Multi-Model Selection); every other stage stays on the
# deployment's configured model.
#
# The split is not about cost, it is about what a selected model is allowed to
# influence. Rewriting a follow-up, deciding whether to search the web,
# classifying tone, and — above all — AUDITING an answer for groundedness are
# machinery: the grounding guarantees depend on them behaving consistently, and
# handing them to whichever free model a member picked from a dropdown would
# make the product's core promise vary per request. A groundedness auditor
# running on an unknown model is worse than no auditor, because it still
# reports a verdict.
#
# It also means one OpenRouter call per question rather than several, which
# matters on a 50-request/day free tier.
#
# EMPATHY_OPENER is here because it is prose prepended to the answer — the
# reader sees one voice, so it must come from the same model as the answer.
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
