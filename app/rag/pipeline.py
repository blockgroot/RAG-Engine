"""The RAG query path: question + org_id -> grounded, tenant-scoped answer.

Composes the Phase 1/2 pieces (embed -> org-scoped retrieve -> gate -> generate).
An *orchestrator*, not a swappable provider, so this package has ``pipeline`` +
``factory`` but no ``base.py``. Two layers keep answers grounded: (1) a confidence
gate — below ``similarity_threshold`` we return the fallback without an LLM call;
(2) a strict prompt (``prompts.py``) that refuses when the context doesn't answer.
Threshold + reasoning: CLAUDE.md §2/§4.

Phase 5 adds two independent capabilities on top, without changing the gate/prompt
logic itself:
- **Conversation memory** — with a ``conversation_id``, the incoming question is
  rewritten into a standalone question (a cheap LLM call) using recent turns + a
  running summary, *before* it enters the retrieval path above.
- **Web-search fallback** — when internal retrieval fails the gate, the model is
  offered a ``web_search`` tool (real function-calling). If it judges the question
  to be about a real external named entity, exactly one bounded search runs and
  the model composes an answer clearly labelled as web-sourced. Anything the model
  deems internal-but-missing still returns the fixed fallback, and a search
  timeout/failure degrades to that same fallback.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

from ..config.settings import MemorySettings, RagSettings, WebSearchSettings
from ..core.exceptions import LLMProviderError, WebSearchError
from ..embeddings.base import EmbeddingProvider
from ..llm.base import LLMProvider
from ..memory.base import ConversationContext, ConversationStore
from ..vectorstore.base import RetrievedChunk, VectorStore
from ..websearch.base import SearchResult, WebSearchProvider
from .prompts import (
    WEB_SEARCH_TOOL,
    build_grounded_prompt,
    build_rewrite_prompt,
    build_summary_prompt,
    build_web_answer_prompt,
    build_web_decision_prompt,
)

# Unmistakable banner so a web-sourced answer never blends with a policy answer.
WEB_ANSWER_LABEL = "🌐 From a web search (NOT your organization's policy documents):"


@dataclass(frozen=True)
class RagResult:
    """The outcome of answering one question for one tenant.

    - ``answer``     the text to show the user (a real answer, or the fixed
      fallback string when we could not ground an answer).
    - ``answered``   ``True`` when an answer was produced (grounded in policy OR
      from web search); ``False`` for the fixed fallback. Callers branch on this
      bool, not on string-matching.
    - ``source``     where the answer came from: ``"policy"`` (internal docs),
      ``"web"`` (web-search fallback), or ``"none"`` (the fixed fallback).
    - ``sources``    the internal chunks retrieved for this question, all belonging
      to the queried ``org_id``. Empty for web answers and the empty case.
    - ``top_score``  similarity of the best retrieved chunk (``None`` if nothing
      was retrieved), for logging / debugging / threshold tuning.
    - ``resolved_question``  the standalone question actually used for retrieval
      after conversation-aware rewriting (``None`` outside a conversation).
    """

    answer: str
    answered: bool
    source: str = "policy"
    sources: list[RetrievedChunk] = field(default_factory=list)
    top_score: float | None = None
    resolved_question: str | None = None


class RagPipeline:
    """Composes embeddings + vector store + LLM into grounded, org-scoped Q&A.

    Prefer building this via ``factory.build_rag_pipeline``. Providers are injected
    (not constructed here) so the pipeline stays a pure orchestrator and is trivial
    to test. ``memory`` and ``web_search`` are optional: when ``None`` the
    corresponding capability is off (leaving the Phase 3 path unchanged).
    """

    def __init__(
        self,
        llm: LLMProvider,
        embedder: EmbeddingProvider,
        store: VectorStore,
        settings: RagSettings | None = None,
        memory: ConversationStore | None = None,
        web_search: WebSearchProvider | None = None,
        memory_settings: MemorySettings | None = None,
        web_search_settings: WebSearchSettings | None = None,
    ) -> None:
        self._llm = llm
        self._embedder = embedder
        self._store = store
        self._settings = settings or RagSettings.from_env()
        self._memory = memory
        self._web_search = web_search
        self._memory_settings = memory_settings or MemorySettings.from_env()
        self._web_search_settings = web_search_settings or WebSearchSettings.from_env()

    # -- public API --------------------------------------------------------

    def answer(
        self, question: str, org_id: str, *, conversation_id: str | None = None
    ) -> RagResult:
        """Answer ``question`` using only ``org_id``'s chunks (with optional memory).

        Retrieval is delegated to the vector store, which enforces the
        ``WHERE org_id`` tenant filter — this pipeline never sees another tenant's
        data and never bypasses that guarantee.
        """
        resolved = question
        if conversation_id is not None and self._memory is not None:
            context = self._memory.get_context(
                conversation_id, self._memory_settings.recent_turns
            )
            if not context.is_empty():
                resolved = self._rewrite_question(question, context)

        result = self._run(resolved, org_id)  # Phase 3 path (+ web fallback)

        if conversation_id is not None and self._memory is not None:
            result = replace(result, resolved_question=resolved)
            self._memory.append_turn(conversation_id, question, result.answer)
            self._maybe_summarize(conversation_id)

        return result

    # -- retrieval / gate / generation (Phase 3 logic, unchanged) ----------

    def _run(self, question: str, org_id: str) -> RagResult:
        # 1) Embed the question and retrieve org-scoped candidates.
        query_vec = self._embedder.embed([question])[0]
        hits = self._store.query(org_id, query_vec, top_k=self._settings.top_k)

        # Nothing stored/matched for this tenant -> gate failed.
        if not hits:
            return self._gate_failed(question, hits=[], top_score=None)

        top_score = hits[0].score

        # 2) Confidence gate (layer 1).
        if top_score < self._settings.similarity_threshold:
            return self._gate_failed(question, hits=hits, top_score=top_score)

        # 3) Grounded generation (layer 2) — unchanged strict-prompt logic.
        prompt = build_grounded_prompt(
            question=question,
            contexts=[h.content for h in hits],
            fallback_response=self._settings.fallback_response,
        )
        raw = self._llm.generate(prompt).strip()
        answered = not self._is_refusal(raw, self._settings.fallback_response)
        answer = raw if answered else self._settings.fallback_response
        return RagResult(
            answer=answer,
            answered=answered,
            source="policy" if answered else "none",
            sources=hits,
            top_score=top_score,
        )

    def _gate_failed(
        self, question: str, hits: list[RetrievedChunk], top_score: float | None
    ) -> RagResult:
        """Gate failed: try the web-search fallback, else the fixed fallback."""
        if self._web_search is not None and self._web_search_settings.enabled:
            web = self._try_web_search(question, top_score)
            if web is not None:
                return web
        return RagResult(
            answer=self._settings.fallback_response,
            answered=False,
            source="none",
            sources=hits,
            top_score=top_score,
        )

    # -- Capability B: single-step web-search tool use ---------------------

    def _try_web_search(self, question: str, top_score: float | None) -> RagResult | None:
        """One decision call + at most one search + one answer call.

        Any failure (model declines, search error/timeout, empty results) returns
        ``None`` so the caller falls back to the fixed internal response.
        """
        decision_prompt = build_web_decision_prompt(
            question, self._settings.fallback_response
        )
        messages = [{"role": "user", "content": decision_prompt}]
        try:
            decision = self._llm.generate_with_tools(
                messages, tools=[WEB_SEARCH_TOOL], tool_choice="auto"
            )
        except LLMProviderError:
            return None

        if not decision.tool_calls:
            return None  # model judged the question internal -> fixed fallback

        query = self._extract_query(decision.tool_calls[0].arguments, question)

        try:
            results = self._web_search.search(
                query,
                max_results=self._web_search_settings.max_results,
                timeout=self._web_search_settings.timeout,
            )
        except WebSearchError:
            return None  # graceful degradation on failure/timeout
        if not results:
            return None

        results_block = "\n\n".join(
            f"[{i + 1}] {r.title}\n{r.snippet}\n{r.url}" for i, r in enumerate(results)
        )
        try:
            raw = self._llm.generate(
                build_web_answer_prompt(question, results_block)
            ).strip()
        except LLMProviderError:
            return None

        return RagResult(
            answer=self._format_web_answer(raw, results),
            answered=True,
            source="web",
            sources=[],
            top_score=top_score,
        )

    @staticmethod
    def _extract_query(arguments: str, default: str) -> str:
        try:
            query = json.loads(arguments).get("query")
        except (json.JSONDecodeError, AttributeError, TypeError):
            query = None
        return query or default

    @staticmethod
    def _format_web_answer(answer: str, results: list[SearchResult]) -> str:
        sources = "\n".join(f"- {r.title} ({r.url})" for r in results if r.url)
        return f"{WEB_ANSWER_LABEL}\n\n{answer}\n\nSources:\n{sources}"

    # -- Capability A: conversation memory helpers -------------------------

    def _rewrite_question(self, question: str, context: ConversationContext) -> str:
        recent = [(t.question, t.answer) for t in context.recent_turns]
        prompt = build_rewrite_prompt(question, context.summary, recent)
        try:
            rewritten = self._llm.generate(prompt).strip()
        except LLMProviderError:
            return question  # never let rewriting break the main path

        # Guard against a model that "helps" by answering instead of rewriting:
        # take the first line and require it to look like a single question.
        first_line = rewritten.splitlines()[0].strip() if rewritten else ""
        if not first_line or len(first_line) > 300 or not first_line.endswith("?"):
            return question  # unreliable rewrite -> fall back to the original
        return first_line

    def _maybe_summarize(self, conversation_id: str) -> None:
        ms = self._memory_settings
        turns = self._memory.get_turns(conversation_id)
        if len(turns) <= ms.summarize_after:
            return
        keep = ms.recent_turns
        to_compress = turns[:-keep] if keep > 0 else turns
        if not to_compress:
            return
        existing = self._memory.get_summary(conversation_id)
        prompt = build_summary_prompt(
            existing, [(t.question, t.answer) for t in to_compress]
        )
        try:
            summary = self._llm.generate(prompt).strip()
        except LLMProviderError:
            return  # summarization is best-effort; skip on failure
        if summary:
            self._memory.set_summary_and_prune(conversation_id, summary, keep)

    @staticmethod
    def _is_refusal(text: str, fallback_response: str) -> bool:
        """True if the model's reply is (essentially) the fixed refusal sentence.

        Robust to trailing punctuation / whitespace / case and to the model
        wrapping the sentence, so a refusal is never mistaken for a real answer.
        """
        core = fallback_response.rstrip(".").strip().lower()
        return core in text.strip().lower()
