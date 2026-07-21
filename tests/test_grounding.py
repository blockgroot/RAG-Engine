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
3. A question that is topically related but not actually answered still triggers
   the fallback — and we assert the top chunk cleared the similarity gate, so it
   is the strict *prompt* (not the gate) refusing. That is the hard case: the
   model must not reason its way from related context to a plausible answer.
"""

from __future__ import annotations

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
    # ...with the correct facts from Org A's policy.
    assert "25" in result.answer, result.answer
    assert "5" in result.answer, result.answer

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
def test_topically_related_but_unanswered_triggers_fallback(rag, store, embedder, org_cleanup):
    """The hard case: Org A's handbook covers annual and sick leave (so a parental
    leave question retrieves on-topic, above-threshold chunks) but says nothing
    about parental leave. The pipeline must still refuse — proving the strict
    prompt, not just the gate, prevents an ungrounded but plausible answer."""
    ids = _seed_two_orgs(store, embedder, org_cleanup)
    org_a = ids[ORG_A_NAME]

    result = rag.answer(
        "What is the company's parental and maternity leave policy, "
        "and how many weeks are paid?",
        org_id=org_a,
    )

    # Must refuse with the exact fallback.
    assert not result.answered, f"expected fallback, model answered: {result.answer!r}"
    assert result.answer == rag._settings.fallback_response

    # And prove this was the PROMPT refusing, not the gate: the retrieved leave
    # content was on-topic enough to clear the similarity threshold.
    assert result.top_score is not None
    assert result.top_score >= rag._settings.similarity_threshold, (
        "expected retrieval to clear the gate so the prompt is what refuses; "
        f"top_score={result.top_score}"
    )
