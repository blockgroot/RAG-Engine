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

from app.config.settings import (
    QueryNormSettings,
    RagSettings,
    RecoverySettings,
    ReuseSettings,
    WebSearchSettings,
)
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

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
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


# --- web after gate-pass + insufficient evidence (deterministic fakes) -----

class _RefuseThenWebLLM(LLMProvider):
    """Grounded generate always refuses; tool-calling always requests web_search."""

    def __init__(self) -> None:
        self.generate_calls = 0
        self.tool_calls = 0

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.generate_calls += 1
        # Recovery expander or web answer composition.
        if "RETRIEVAL EXPRESSIONS:" in prompt:
            return "external protest participation laws India"
        if "SEARCH RESULTS:" in prompt or "web SEARCH RESULTS" in prompt.lower():
            return "Public reporting describes CJP as a civil-rights campaign; participation rules depend on local law."
        # Grounded policy prompt → refuse (insufficient internal evidence).
        return RagSettings.from_env().fallback_response

    def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
        self.tool_calls += 1
        return ChatResult(
            text=None,
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="web_search",
                    arguments='{"query": "CJP protest corporate employees India"}',
                )
            ],
            raw_message={"role": "assistant"},
        )


class _OkWebSearch(WebSearchProvider):
    def search(self, query, max_results=5, timeout=8.0):
        from app.websearch.base import SearchResult

        return [
            SearchResult(
                title="Example news",
                snippet="Background on political protests in India.",
                url="https://example.com/protest",
            )
        ]


def test_web_after_insufficient_evidence_when_gate_passed():
    """Gate can clear on weak neighbors; after refuse (+ optional recovery) still offer web.

    Models the Niva Bupa / CJP case: top_score above threshold, generation finds
    evidence insufficient, then web is offered for an external named entity.
    """
    from tests.fakes import KeywordEmbedder, TopicAwareVectorStore

    org = "org-web-insuf"
    # Chunk shares a loose topic so first retrieve clears a low gate; content does
    # not answer the external protest question.
    # No topic overlap with the question → weak neighbor still returned above the
    # real 0.35 gate (same shape as office-parties chunks for a protest question).
    store = TopicAwareVectorStore(
        org,
        chunks=[("doc-1", "leave wellness unrelated handbook filler")],
        weak_fallback_content="office parties and complaint mechanism guidelines",
        weak_fallback_score=0.50,
    )
    llm = _RefuseThenWebLLM()
    pipe = RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(
            top_k=3,
            similarity_threshold=0.35,
            fallback_response=RagSettings.from_env().fallback_response,
        ),
        memory=None,
        web_search=_OkWebSearch(),
        web_search_settings=WebSearchSettings(
            enabled=True, provider="duckduckgo", api_key=None, max_results=3, timeout=2.0
        ),
        retriever=None,
        recovery_settings=RecoverySettings(enabled=True, max_queries=1),
    )

    result = pipe.answer(
        "Can corporate employees participate in recent CJP protests in India?",
        org_id=org,
    )

    assert result.source == "web", result
    assert result.answered
    assert WEB_ANSWER_LABEL in result.answer
    assert llm.tool_calls >= 1


class _CaptureSearchLLM(LLMProvider):
    """Always requests web_search; records the decision prompt + search query."""

    def __init__(self) -> None:
        self.decision_prompts: list[str] = []
        self.search_queries: list[str] = []

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        return "Cigna is a major US health insurer offering medical plans."

    def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
        content = messages[0]["content"]
        self.decision_prompts.append(content)
        # Extract a search query that preserves the entity from the user question.
        query = "Cigna health insurance coverage"
        if "Niva" in content:
            query = "Niva Bupa claim settlement ratio"
        self.search_queries.append(query)
        return ChatResult(
            text=None,
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="web_search",
                    arguments=f'{{"query": "{query}"}}',
                )
            ],
            raw_message={"role": "assistant"},
        )


class _OkSearch(WebSearchProvider):
    def search(self, query, max_results=5, timeout=8.0):
        from app.websearch.base import SearchResult

        return [
            SearchResult(
                title="About the insurer",
                snippet="External coverage overview.",
                url="https://example.com/insurer",
            )
        ]


@requires_db
def test_query_norm_preserves_entities_for_web_path(embedder, store, org_cleanup):
    """Phase 17 must not corrupt Phase 5 named-entity web-search.

    SymSpell runs on the retrieval key only; the web decision still sees the
    original question. Both the normalized retrieval key AND the web decision
    prompt must keep external entity tokens intact.
    """
    from app.rag.query_normalize import CorpusSpellNormalizer

    org_id = _seed_leave_org(store, embedder, org_cleanup)
    # Corpus contains "company" / "five"-like distractors via leave text only —
    # add words that previously attracted distance-2 false fixes.
    extra = chunk_text(
        preprocess(
            "# Misc\nCompany handbook. Five modules. Rated vendors. Main campus."
        )
    )
    store.add_document(org_id, "Misc", extra, embedder.embed(extra))

    texts = store.list_chunk_texts(org_id)
    # Prove the default normalizer leaves entities intact (distance 1 + Cap skip).
    norm = CorpusSpellNormalizer(
        QueryNormSettings(enabled=True, max_edit_distance=1, min_word_length=4)
    )
    for q in (
        "What does Cigna health insurance generally cover?",
        "What is Niva Bupa's claim settlement ratio?",
    ):
        assert norm.normalize(q, org_id, texts) == q

    llm = _CaptureSearchLLM()
    pipe = RagPipeline(
        llm=llm,
        embedder=embedder,
        store=store,
        settings=RagSettings(
            top_k=3, similarity_threshold=0.35, fallback_response=(
                "I don't have information on that in the available policy documents."
            )
        ),
        memory=None,
        web_search=_OkSearch(),
        web_search_settings=WebSearchSettings(
            enabled=True, provider="duckduckgo", api_key=None, max_results=3, timeout=2.0
        ),
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
        query_norm_settings=QueryNormSettings(
            enabled=True, max_edit_distance=1, min_word_length=4
        ),
    )

    for q, entity in (
        ("What does Cigna health insurance generally cover?", "Cigna"),
        ("What is Niva Bupa's claim settlement ratio?", "Niva"),
    ):
        result = pipe.answer(q, org_id)
        assert result.source == "web", (q, result.source, result.answer)
        assert entity in llm.decision_prompts[-1]
        assert entity in llm.search_queries[-1] or entity.lower() in llm.search_queries[-1].lower()
