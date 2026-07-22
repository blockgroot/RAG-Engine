"""Golden-set path-firing checks — the deterministic, every-push regression tier.

These are the cheap half of the Phase 7 evaluation: for each golden case, run it
through a ``PolicyAgent`` and assert the *right path fired* (policy answer /
internal fallback / web search) and, for answerable cases, that the expected facts
are present. No LLM-as-judge — the expensive RAGAS scoring lives in
``evaluation.run_eval --ragas`` and runs less often (see evaluation/README.md).

They reuse the session-scoped provider fixtures from ``conftest.py`` and seed one
throwaway org from the golden ``CORPUS`` for the module, mirroring the existing
grounding/conversation tests. The DB + LLM prerequisites are the same as the rest
of the suite; the ``web`` cases additionally need the network and are marked
``network`` so CI's fast tier can deselect them (``-m "not network"``).
"""

from __future__ import annotations

import uuid

import pytest

from app.agent import build_policy_agent
from app.db import get_connection
from app.websearch import build_web_search_provider
from evaluation.golden_set import cases_by_category
from evaluation.harness import run_case, run_case_stable, seed_corpus
from .conftest import requires_db, requires_llm

# Deterministic cases (no network): answerable + fallback + conversation.
DETERMINISTIC_CASES = (
    cases_by_category("answerable")
    + cases_by_category("fallback")
    + cases_by_category("conversation")
)
WEB_CASES = cases_by_category("web")


@pytest.fixture(scope="module")
def golden_org(store, embedder):
    """Seed one org with the golden CORPUS for the whole module; cascade-delete it."""
    org_id = seed_corpus(store, embedder, f"Golden Test-{uuid.uuid4().hex[:8]}")
    yield org_id
    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))


@pytest.fixture(scope="module")
def golden_agent(embedder, store, memory, retriever):
    """Production-config agent for the deterministic tier: memory ON, web OFF (no
    network), reusing the shared hybrid+rerank retriever."""
    return build_policy_agent(
        embedder=embedder, store=store, memory=memory, web_search=None, retriever=retriever
    )


@pytest.fixture(scope="module")
def golden_web_agent(embedder, store, retriever):
    """Agent with the real web-search tool ON (memory OFF) for the web cases."""
    return build_policy_agent(
        embedder=embedder,
        store=store,
        memory=None,
        web_search=build_web_search_provider(),
        retriever=retriever,
    )


@requires_db
@requires_llm
@pytest.mark.parametrize("case", DETERMINISTIC_CASES, ids=lambda c: c.id)
def test_golden_path_fires(case, golden_agent, memory, golden_org):
    """The expected path fires and expected facts are present.

    Uses ``run_case_stable`` so a single stochastic LLM refusal on a
    non-deterministic endpoint retries once; a genuine regression still fails
    (both attempts fail). See ``harness._RETRYABLE``.
    """
    result = run_case_stable(golden_agent, golden_org, case, memory=memory)

    assert result.path_ok, (
        f"{case.id}: expected source={case.expected_source}, got source={result.source} "
        f"(grounded={result.grounded}, top_score={result.top_score}); answer={result.answer!r}"
    )
    if result.facts_ok is not None:
        assert result.facts_ok, (
            f"{case.id}: missing expected facts {case.expected_facts} in answer: {result.answer!r}"
        )
    if result.resolved_ok is not None:
        assert result.resolved_ok, (
            f"{case.id}: rewrite {result.resolved_question!r} missing {case.resolved_contains}"
        )


@requires_db
@requires_llm
@pytest.mark.network
@pytest.mark.parametrize("case", WEB_CASES, ids=lambda c: c.id)
def test_golden_web_path_fires(case, golden_web_agent, golden_org):
    """A real external named entity trips the web-search path (not a policy answer).

    Network-dependent (DuckDuckGo rate-limits): a search failure degrades to the
    internal fallback by design, so we accept ``web`` (search succeeded) or ``none``
    (graceful degradation) but never ``policy`` — the agent must never fabricate a
    policy answer for an external entity.
    """
    result = run_case(golden_web_agent, golden_org, case)
    assert result.source in ("web", "none"), (
        f"{case.id}: expected web or graceful fallback, got {result.source}: {result.answer!r}"
    )
    assert result.source != "policy", f"{case.id}: external entity answered as policy!"
