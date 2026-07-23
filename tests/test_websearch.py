"""Web-search fallback tests (Phase 5, Capability B).

Prove that when internal retrieval fails the confidence gate:
- a question about a real, named, EXTERNAL entity triggers a web search and
  returns a well-formed answer clearly labelled as web-sourced;
- a question that is clearly meant to be INTERNAL (just not in the docs) still
  returns the plain fixed fallback, with no web search;
- a web-search timeout/failure degrades gracefully to that same fixed fallback
  (deterministic: a fake LLM that always calls the tool + a failing search).
"""

from __future__ import annotations

import uuid

from app.config.settings import RagSettings, RecoverySettings, WebSearchSettings
from app.core.exceptions import WebSearchError
from app.ingestion import chunk_text, preprocess
from app.llm.base import ChatResult, LLMProvider, ToolCall
from app.rag.pipeline import WEB_ANSWER_LABEL, RagPipeline
from app.websearch.base import WebSearchProvider
from .conftest import requires_db, requires_llm

# Internal policy docs deliberately say nothing about any external entity.
LEAVE_DOC = """
# Paid Annual Leave
Full-time employees are entitled to 25 days of paid annual leave per year.
Up to 5 unused days may be carried over into the following year.
"""


def _seed_leave_org(store, embedder, org_cleanup):
    org_id = store.create_organization(f"Web Co-{uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    chunks = chunk_text(preprocess(LEAVE_DOC))
    store.add_document(org_id, "Leave Policy", chunks, embedder.embed(chunks))
    return org_id


@requires_db
@requires_llm
def test_external_named_entity_triggers_web_search(rag_web, store, embedder, org_cleanup):
    org_id = _seed_leave_org(store, embedder, org_cleanup)

    result = rag_web.answer(
        "What does Cigna health insurance generally cover?", org_id
    )

    # A real external entity not in our docs -> web-sourced, clearly labelled.
    assert result.source == "web", f"expected a web answer, got source={result.source!r}: {result.answer!r}"
    assert result.answered
    assert WEB_ANSWER_LABEL in result.answer, result.answer
    assert "Sources:" in result.answer


@requires_db
@requires_llm
def test_internal_only_question_still_falls_back_without_web(rag_web, store, embedder, org_cleanup):
    org_id = _seed_leave_org(store, embedder, org_cleanup)

    result = rag_web.answer(
        "What is our company's internal policy on subsidised on-site gym memberships?",
        org_id,
    )

    # Clearly internal-but-missing -> plain fixed fallback, NOT a web search.
    assert result.source == "none", f"expected internal fallback, got {result.source!r}: {result.answer!r}"
    assert not result.answered
    assert result.answer == rag_web._settings.fallback_response
    assert WEB_ANSWER_LABEL not in result.answer


# --- deterministic graceful-degradation test (no network, no real LLM) -----

class _AlwaysSearchLLM(LLMProvider):
    """Fake LLM that always asks to call web_search (so we can test the failure
    path deterministically), and returns fixed text for plain generate()."""

    def generate(self, prompt: str) -> str:
        return "unused in this path"

    def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
        return ChatResult(
            text=None,
            tool_calls=[ToolCall(id="call_1", name="web_search", arguments='{"query": "anything"}')],
            raw_message={"role": "assistant"},
        )


class _FailingWebSearch(WebSearchProvider):
    """Web search that always fails/times out."""

    def search(self, query, max_results=5, timeout=8.0):
        raise WebSearchError("simulated timeout")


@requires_db
def test_web_search_failure_degrades_to_fixed_fallback(embedder, store, org_cleanup):
    # Empty org -> retrieval returns nothing -> gate fails -> web path attempted.
    org_id = store.create_organization(f"Fail Co-{uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    pipe = RagPipeline(
        llm=_AlwaysSearchLLM(),
        embedder=embedder,
        store=store,
        memory=None,
        web_search=_FailingWebSearch(),
        web_search_settings=WebSearchSettings(enabled=True, provider="duckduckgo", api_key=None, max_results=5, timeout=1.0),
        recovery_settings=RecoverySettings(enabled=False),
    )

    result = pipe.answer("What does Acme External Insurance Corp cover?", org_id)

    # The search raised -> we must land on the fixed fallback, not crash/hang.
    assert result.source == "none"
    assert not result.answered
    assert result.answer == RagSettings.from_env().fallback_response
