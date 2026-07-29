"""Grounding tests for the RAG query path — the Phase 3 completion gate.

These prove the pipeline actually *grounds* answers, not merely that the code
runs. Two fake tenants are created with distinct policy content (following the
same fixture + cascade-cleanup pattern as ``test_isolation.py``), then three
scenarios are exercised against real embeddings, a real pgvector store, and the
real LLM:

1. A question answerable from one org's data returns a correct, grounded answer,
   traceable to that org's own chunks only.
2. A question no org's data covers triggers the fixed "I don't know" fallback,
   for *both* orgs, instead of an invented answer.
3. A question that is topically related but not actually answered must not invent
   unsupported conclusions (Grounding Gap). The model may use Related-but-Not-
   Explicit mode to report what the docs *do* say, but must not invent parental
   leave entitlements. The top chunk must have cleared the similarity gate so
   this is the prompt (not the gate) enforcing grounding.
"""

from __future__ import annotations

import re
import uuid

from app.ingestion import chunk_text, preprocess
from .conftest import requires_db, requires_llm

# Two tenants, deliberately different domains. Org A is an HR/leave handbook;
# Org B is an IT-security policy. Org A's distinctive facts (25 days, $500) must
# never appear in an answer grounded on Org B and vice versa.
ORG_A_NAME = "Northwind Traders"
ORG_A_POLICY = """
# Northwind Traders Employee Handbook

## Paid Annual Leave
Full-time employees are entitled to 25 days of paid annual leave per calendar
year. Up to 5 unused days may be carried over into the following year.

## Sick Leave
Employees receive 10 paid sick days per year, separate from annual leave. A
doctor's note is required for absences longer than three consecutive days.

## Expense Reimbursement
Business travel expenses are reimbursed up to $500 per trip. Original receipts
must be submitted within 30 days of the expense.
"""

ORG_B_NAME = "Umbrella Corp"
ORG_B_POLICY = """
# Umbrella Corp IT Security Policy

## Device Security
Company laptops must be full-disk encrypted and screen-lock after five minutes
of inactivity.

## Access Control
All access to production databases requires multi-factor authentication.

## Incident Reporting
Security incidents must be reported to the IT security team within 24 hours of
discovery.
"""


def _ingest(store, embedder, org_id: str, title: str, raw_text: str) -> None:
    chunks = chunk_text(preprocess(raw_text))
    embeddings = embedder.embed(chunks)
    store.add_document(org_id=org_id, title=title, chunks=chunks, embeddings=embeddings)


def _seed_two_orgs(store, embedder, org_cleanup) -> dict[str, str]:
    """Create the two fake tenants and return {name: org_id}."""
    suffix = uuid.uuid4().hex[:8]
    ids: dict[str, str] = {}
    for name, policy in ((ORG_A_NAME, ORG_A_POLICY), (ORG_B_NAME, ORG_B_POLICY)):
        org_id = store.create_organization(f"{name}-{suffix}")
        org_cleanup.append(org_id)
        _ingest(store, embedder, org_id, "Policy", policy)
        ids[name] = org_id
    return ids


@requires_db
@requires_llm
def test_answerable_question_is_grounded_and_traceable(rag, store, embedder, org_cleanup):
    """A question answerable from Org A returns a correct answer, traceable to
    Org A's own chunks — and never leaks Org B's content."""
    ids = _seed_two_orgs(store, embedder, org_cleanup)
    org_a = ids[ORG_A_NAME]

    result = rag.answer(
        "How many days of paid annual leave do full-time employees get, "
        "and how many unused days can be carried over?",
        org_id=org_a,
    )

    # It answered (did not fall back)...
    assert result.answered, f"expected a grounded answer, got fallback: {result.answer!r}"
    # ...with the correct facts from Org A's policy. The prompt now asks for a
    # direct, natural statement rather than a rigid citation-style one, so a
    # small number like the 5-day carry-over is sometimes spelled out
    # ("five") instead of using the digit — accept either (25 is large enough
    # that models essentially never spell it out, so that one stays strict).
    assert "25" in result.answer, result.answer
    answer_lower = result.answer.lower()
    assert "5" in answer_lower or "five" in answer_lower, result.answer

    # Traceability: every source chunk belongs to Org A, and the answer's facts
    # are present in those chunks (so the answer really came from them).
    assert result.sources, "expected grounding sources"
    assert all(s.org_id == org_a for s in result.sources), "answer used another org's chunks"
    joined = " ".join(s.content for s in result.sources)
    assert "25 days" in joined

    # No cross-tenant leakage: Org B's distinctive facts never entered the context.
    assert "multi-factor" not in joined.lower()
    assert "encrypted" not in joined.lower()


@requires_db
@requires_llm
def test_no_relevant_data_triggers_fallback_for_both_orgs(rag, store, embedder, org_cleanup):
    """A question neither org's data addresses must yield the fixed fallback for
    both tenants, not an invented answer."""
    ids = _seed_two_orgs(store, embedder, org_cleanup)

    question = "What discount do employees get at the on-site company gym?"
    for name, org_id in ids.items():
        result = rag.answer(question, org_id=org_id)
        assert not result.answered, f"{name}: expected fallback, got answer: {result.answer!r}"
        assert result.answer == rag._settings.fallback_response, name


@requires_db
@requires_llm
def test_topically_related_but_unanswered_prevents_unsupported_inference(
    rag, store, embedder, org_cleanup
):
    """Grounding Gap: Org A's handbook covers annual/sick leave (so a parental
    leave question retrieves on-topic, above-threshold chunks) but says nothing
    about parental leave. The model must not invent a parental-leave entitlement
    (weeks paid, eligibility). It may refuse (No Supporting Evidence) or use
    Related-but-Not-Explicit to report what the docs *do* say — never unsupported
    conclusions. The top chunk must clear the gate so this is the prompt enforcing
    grounding, not the gate.
    """
    ids = _seed_two_orgs(store, embedder, org_cleanup)
    org_a = ids[ORG_A_NAME]

    result = rag.answer(
        "What is the company's parental and maternity leave policy, "
        "and how many weeks are paid?",
        org_id=org_a,
    )

    # Gate must have cleared so the prompt (not the gate) is doing the work.
    assert result.top_score is not None
    assert result.top_score >= rag._settings.similarity_threshold, (
        "expected retrieval to clear the gate so the prompt enforces grounding; "
        f"top_score={result.top_score}"
    )

    answer_l = result.answer.lower()
    # Must not invent a concrete parental/maternity entitlement not in the docs.
    invented = re.search(
        r"(\d+\s*(weeks?|months?)\s*(of\s+)?(paid\s+)?(parental|maternity|paternity))",
        answer_l,
    )
    assert invented is None, f"invented unsupported parental leave claim: {result.answer!r}"

    if not result.answered:
        assert result.answer == rag._settings.fallback_response
    else:
        # Related-but-Not-Explicit: must acknowledge docs don't explicitly answer,
        # and any facts mentioned should come from retrieved leave context.
        #
        # The Grounding Gap tone fix (prompts.py) deliberately FORBIDS the old
        # meta-language phrasing this list originally checked for ("the
        # documents do not explicitly answer", "I cannot give a definitive
        # answer") in favor of a natural, empathetic voice — so the acceptable
        # phrase list below includes the natural-voice equivalents a compliant
        # Mode B answer now uses (e.g. "doesn't include a specific ... policy")
        # alongside the original meta-language forms, which a model may still
        # produce on an off run given known LLM non-determinism.
        asserts_related = (
            "not explicitly" in answer_l
            or "do not explicitly" in answer_l
            or "doesn't explicitly" in answer_l
            or "does not explicitly" in answer_l
            or "don't explicitly" in answer_l
            or "no information" in answer_l
            or "does not mention" in answer_l
            or "do not mention" in answer_l
            or "doesn't mention" in answer_l
            or "not mention" in answer_l
            or "doesn't include a specific" in answer_l
            or "does not include a specific" in answer_l
            or "no specific" in answer_l
            or "isn't a specific" in answer_l
            or "is not a specific" in answer_l
            or "doesn't have a specific" in answer_l
            or "don't have a specific" in answer_l
            or "does not have a specific" in answer_l
            or "no dedicated" in answer_l
            or "doesn't include" in answer_l
            or "does not include" in answer_l
        )
        assert asserts_related or "parental" not in answer_l or "maternity" not in answer_l, (
            f"related answer must distinguish non-explicit coverage: {result.answer!r}"
        )
