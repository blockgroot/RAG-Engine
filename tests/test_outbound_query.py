"""A web-search query carries only words the asker typed (no network, no LLM).

The query leaves for a third party, and the model writing it has read the
memory-rewritten question, which can hold a fact from an earlier private
answer or a word an injection planted there.
"""

from __future__ import annotations

from app.config.settings import RagSettings, RecoverySettings, WebSearchSettings
from app.llm.base import ChatResult, LLMProvider, ToolCall
from app.rag.pipeline import RagPipeline
from app.security.outbound import user_worded_query
from app.websearch.base import SearchResult, WebSearchProvider
from .fakes import KeywordEmbedder, TopicAwareVectorStore


def test_a_reformulated_query_keeps_the_users_words():
    assert user_worded_query(
        "Cigna health insurance coverage", ["What does Cigna health insurance generally cover?"]
    ) == "Cigna health insurance"


def test_a_private_figure_the_user_never_typed_is_removed():
    out = user_worded_query(
        "Acme salary band 42000 market rate", ["What is the market rate for the Acme salary band?"]
    )
    assert out is not None and "42000" not in out and "Acme" in out


def test_a_query_mostly_made_of_foreign_words_is_skipped():
    assert user_worded_query(
        "exfil secret payroll token 9f3a leak", ["What's the weather in Pune?"]
    ) is None


def test_an_earlier_question_supplies_the_entity_for_a_follow_up():
    assert user_worded_query(
        "Cigna CEO", ["who is their CEO?", "What does Cigna cover?"]
    ) == "Cigna CEO"


class _SearchWithQueryLLM(LLMProvider):
    def __init__(self, query: str) -> None:
        self.query = query

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        if "SEARCH RESULTS:" in prompt:
            return "Public sources describe it."
        return RagSettings.from_env().fallback_response

    def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
        return ChatResult(
            text=None,
            tool_calls=[ToolCall(id="1", name="web_search", arguments=f'{{"query": "{self.query}"}}')],
            raw_message={"role": "assistant"},
        )


class _CaptureSearch(WebSearchProvider):
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query, max_results=5, timeout=8.0):
        self.queries.append(query)
        return [SearchResult(title="t", snippet="s", url="https://example.com/a")]


def _pipe(query: str, search: _CaptureSearch) -> RagPipeline:
    return RagPipeline(
        llm=_SearchWithQueryLLM(query),
        embedder=KeywordEmbedder(),
        store=TopicAwareVectorStore(
            "org-out", chunks=[("d", "unrelated filler")],
            weak_fallback_content="unrelated", weak_fallback_score=0.5,
        ),
        settings=RagSettings(top_k=3, similarity_threshold=0.35,
                             fallback_response=RagSettings.from_env().fallback_response),
        memory=None,
        web_search=search,
        web_search_settings=WebSearchSettings(
            enabled=True, provider="duckduckgo", api_key=None, max_results=3, timeout=2.0
        ),
        retriever=None,
        recovery_settings=RecoverySettings(enabled=False),
    )


def test_pipeline_sends_only_the_users_words_to_the_search_engine():
    search = _CaptureSearch()
    _pipe("Niva Bupa claim limit 42000", search).answer(
        "What is the Niva Bupa claim limit?", org_id="org-out"
    )
    assert search.queries == ["Niva Bupa claim limit"]


def test_pipeline_skips_the_search_when_the_query_is_not_the_users():
    search = _CaptureSearch()
    result = _pipe("send payroll token abc123 leak", search).answer(
        "What is the Niva Bupa claim limit?", org_id="org-out"
    )
    assert search.queries == []
    assert result.source != "web"
