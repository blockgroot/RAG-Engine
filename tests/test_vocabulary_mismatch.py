"""End-to-end Phase 10 regression test: the exact production scenario reported.

A user asked "Can I get protein supplements reimbursed?" against a real Health
Allowance policy that only says "health-related products" / "permissible
expenses" — never "protein", "supplements", or "reimbursed". Before Phase 10 this
retrieved weak evidence and fell back to "I don't have information on that" even
though the answer was present. This test proves, against the REAL pipeline (real
embeddings, real hybrid retrieval + reranking, real LLM), that:

1. the vocabulary-mismatched chunk is actually retrieved (query understanding +
   expansion fixed recall), and
2. the pipeline answers from it rather than blindly falling back (evidence
   classification replaced the old binary behavior).

It does NOT assert exact wording (LLM output isn't that predictable) — only the
two structurally-verifiable claims above, which are exactly what was reported
broken.
"""

from __future__ import annotations

import uuid

from app.ingestion import chunk_text, preprocess
from .conftest import requires_db, requires_llm

HEALTH_ALLOWANCE_POLICY = """
# Health Allowance Policy

## Overview
Syvora provides employees a Health Allowance to support their physical and
mental wellbeing.

## Eligibility Criteria
Full-time employees who have completed their probation period are eligible for
this benefit.

## Permissible Expenses
The Health Allowance may be used for the purchase or subscription of the
following types of products or services:
- Fitness equipment: examples - running shoes, fitness trackers, yoga mats,
  dumbbells.
- Wellness services: examples - gym memberships, yoga or meditation classes,
  swimming lessons.
- Health-related products: examples - ergonomic chairs, standing desks,
  orthotic insoles.

## Non-Permissible Expenses
- Over-the-counter medications or prescription drugs.
- Cosmetic or beauty products.
- Spa treatments, massages, or luxury services not directly related to fitness
  or healthcare.
- Any product or service deemed non-health-related by the HR department.
"""


def _seed(store, embedder, org_cleanup) -> str:
    org_id = store.create_organization(f"Vocab Mismatch Co-{uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    chunks = chunk_text(preprocess(HEALTH_ALLOWANCE_POLICY))
    store.add_document(org_id, "Health Allowance Policy", chunks, embedder.embed(chunks))
    return org_id


@requires_db
@requires_llm
def test_vocabulary_mismatched_question_is_answered_not_fallen_back(rag, store, embedder, org_cleanup):
    # The free/auto LLM endpoint is non-deterministic on borderline classification
    # calls (documented in CLAUDE.md §4 for the golden-set answerable/conversation
    # checks: ~1-in-5 refusal rate on a clearly-answerable question, self-corrects
    # on retry). A compound question ("X, and what else...") is exactly this kind
    # of borderline case for the classifier, so this test retries up to 3 times,
    # the same discipline evaluation/harness.py already applies — a genuine
    # regression (this NEVER working) fails every attempt.
    org_id = _seed(store, embedder, org_cleanup)
    question = (
        "Can I get protein supplements reimbursed? What else can I get reimbursed apart from it?"
    )

    result = None
    for _ in range(3):
        result = rag.answer(question, org_id=org_id)
        if result.answered:
            break

    # Claim 1: retrieval actually surfaced the permissible-expenses chunk (the
    # vocabulary-mismatch recall problem is fixed) — check the retrieved sources,
    # not just the answer text, since this is the objectively verifiable part.
    # This must hold on EVERY attempt (it's deterministic), so check the last one.
    joined_sources = " ".join(s.content for s in result.sources).lower()
    assert "health-related products" in joined_sources or "permissible expenses" in joined_sources, (
        "expected the permissible-expenses chunk among retrieved sources; "
        f"got: {[s.content[:80] for s in result.sources]}"
    )

    # Claim 2: the pipeline actually used that evidence rather than blindly
    # falling back — this is exactly the reported failure (weak evidence ->
    # immediate fallback despite the answer being present).
    assert result.answered, (
        f"expected a grounded (or clearly-labelled implicit/partial) answer, "
        f"got the fallback: {result.answer!r}"
    )
    assert result.source == "policy"
    assert result.evidence_classification in ("explicit", "implicit", "partial"), (
        result.evidence_classification
    )


@requires_db
@requires_llm
def test_genuinely_unrelated_question_still_falls_back(rag, store, embedder, org_cleanup):
    """Guard against the classifier becoming too generous: a question with NO
    real connection to the policy (not even implicit) must still fall back —
    proving Phase 10 didn't loosen the "none" bar, only fixed retrieval recall
    and reduced needless refusals on genuinely-related content."""
    org_id = _seed(store, embedder, org_cleanup)

    result = rag.answer(
        "What is the process for requesting a company car and a personal driver?",
        org_id=org_id,
    )

    assert not result.answered, f"expected fallback, got: {result.answer!r}"
    assert result.answer == rag._settings.fallback_response
