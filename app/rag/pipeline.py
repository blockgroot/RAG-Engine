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

Phase 8 refines two earlier pieces, again without touching the gate/prompt:
- **Incremental summarization** — the running summary is updated after *every*
  turn by folding in only the single turn that just fell out of the verbatim
  window (``_update_running_summary``), instead of bulk-summarizing older turns
  once a threshold is crossed. Each update's input is the existing summary + one
  turn, so its cost stays small and roughly constant however long the chat gets.
- **Retrieval reuse** — before retrieval runs on a follow-up, a cheap *non-LLM*
  cosine check (``_try_reuse``) compares the rewritten question against the
  previous turn's retrieved chunks; if they still cover it, those chunks are reused
  and retrieval is skipped. The reused chunks then flow through the *unchanged*
  gate → generate path, so this only avoids redundant retrieval — it never
  weakens or bypasses the confidence gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace

import numpy as np

from ..config.settings import MemorySettings, RagSettings, ReuseSettings, WebSearchSettings
from ..core.exceptions import LLMProviderError, WebSearchError
from ..embeddings.base import EmbeddingProvider
from ..llm.base import LLMProvider
from ..memory.base import ConversationContext, ConversationStore, RetrievedChunkRecord
from ..vectorstore.base import RetrievedChunk, VectorStore
from ..websearch.base import SearchResult, WebSearchProvider
from .retrieval import HybridRetriever, RetrievalResult
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
    - ``retrieval_reused``  ``True`` when this turn skipped retrieval and reused the
      previous turn's chunks (Phase 8). A diagnostic for logging/tests; the answer
      itself is produced by the same gate → generate path either way.
    """

    answer: str
    answered: bool
    source: str = "policy"
    sources: list[RetrievedChunk] = field(default_factory=list)
    top_score: float | None = None
    resolved_question: str | None = None
    retrieval_reused: bool = False


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
        retriever: "HybridRetriever | None" = None,
        reuse_settings: ReuseSettings | None = None,
    ) -> None:
        self._llm = llm
        self._embedder = embedder
        self._store = store
        self._settings = settings or RagSettings.from_env()
        self._memory = memory
        self._web_search = web_search
        self._memory_settings = memory_settings or MemorySettings.from_env()
        self._web_search_settings = web_search_settings or WebSearchSettings.from_env()
        # Phase 8: retrieval-reuse gate (a cheap non-LLM cosine check). Only active
        # inside a conversation (needs a previous turn's chunks) and when enabled.
        self._reuse_settings = reuse_settings or ReuseSettings.from_env()
        # Phase 6: hybrid + reranking retriever. When None, fall back to plain
        # vector search (the Phase 3 behaviour), keeping this pipeline usable
        # without the retrieval upgrades.
        self._retriever = retriever

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

        # Phase 3 path (+ Phase 8 retrieval reuse + web fallback).
        result = self._run(resolved, org_id, conversation_id=conversation_id)

        if conversation_id is not None and self._memory is not None:
            result = replace(result, resolved_question=resolved)
            self._memory.append_turn(conversation_id, question, result.answer)
            # Remember this turn's policy chunks so the next turn can try to reuse
            # them (web/fallback answers have no reusable policy chunks -> cleared).
            self._remember_retrieval(conversation_id, org_id, result)
            self._update_running_summary(conversation_id)

        return result

    # -- retrieval / gate / generation (Phase 3 logic, unchanged) ----------

    def _run(
        self, question: str, org_id: str, *, conversation_id: str | None = None
    ) -> RagResult:
        # We embed the question once and use that vector for both the (Phase 8)
        # reuse check and, if we do retrieve, the vector search — no extra cost.
        query_vec = self._embedder.embed([question])[0]

        # 0) Phase 8 retrieval reuse: if the previous turn's chunks still cover this
        #    question (cheap cosine check, no LLM, no retrieval), reuse them. The
        #    `reuse_score` it returns is a genuine cosine similarity of THIS question
        #    vs the best reused chunk, so it feeds the unchanged gate below exactly
        #    like a fresh `top_score` would.
        reused = self._try_reuse(question, org_id, query_vec, conversation_id)
        if reused is not None:
            hits, top_score = reused.hits, reused.gate_score
            retrieval_reused = True
        else:
            retrieval_reused = False
            # 1) Retrieve org-scoped candidates. With a HybridRetriever this is
            #    vector + keyword (RRF-fused) then cross-encoder reranked; without one
            #    it's plain vector search (Phase 3). Either way `top_score` is the best
            #    cosine similarity, so the confidence gate below is unchanged.
            if self._retriever is not None:
                retrieval = self._retriever.retrieve(org_id, question, query_vec)
                hits = retrieval.hits
                top_score = retrieval.gate_score
            else:
                hits = self._store.query(org_id, query_vec, top_k=self._settings.top_k)
                top_score = hits[0].score if hits else None

        # Nothing stored/matched for this tenant -> gate failed.
        if not hits or top_score is None:
            return self._gate_failed(question, hits=[], top_score=None)

        # 2) Confidence gate (layer 1) — runs identically whether hits were freshly
        #    retrieved or reused, so reuse never bypasses or weakens the gate.
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
            retrieval_reused=retrieval_reused,
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

    # -- Phase 8: retrieval reuse (a cheap, deterministic, non-LLM check) ---

    def _try_reuse(
        self,
        question: str,
        org_id: str,
        query_vec: list[float],
        conversation_id: str | None,
    ) -> RetrievalResult | None:
        """Decide whether to reuse the previous turn's chunks instead of retrieving.

        Purely a similarity comparison in code — no LLM call, the same kind of
        deterministic gate as the Phase 3 confidence threshold. Returns a
        ``RetrievalResult`` (reused chunks re-scored against THIS question, best
        first) when the best cosine similarity clears ``reuse.threshold``, else
        ``None`` so the caller retrieves normally.
        """
        if (
            conversation_id is None
            or self._memory is None
            or not self._reuse_settings.enabled
        ):
            return None

        prev = self._memory.get_last_retrieval(conversation_id)
        prev = [r for r in prev if r.org_id == org_id]  # never cross a tenant boundary
        if not prev:
            return None

        # Recompute embeddings for the handful of previous chunks (cheap, local,
        # deterministic) rather than storing 1024-dim vectors in the DB.
        prev_vecs = self._embedder.embed([r.content for r in prev])
        sims = [self._cosine(query_vec, v) for v in prev_vecs]
        best = max(sims)
        if best < self._reuse_settings.threshold:
            return None  # not confidently covered -> retrieve fresh

        order = sorted(range(len(prev)), key=lambda i: sims[i], reverse=True)
        hits = [
            RetrievedChunk(
                content=prev[i].content,
                score=sims[i],  # fresh cosine of THIS question vs the reused chunk
                document_id=prev[i].document_id,
                chunk_index=prev[i].chunk_index,
                org_id=prev[i].org_id,
            )
            for i in order
        ][: self._settings.top_k]
        # gate_score == best cosine among reused chunks, mirroring fresh retrieval.
        return RetrievalResult(hits=hits, gate_score=best)

    def _remember_retrieval(
        self, conversation_id: str, org_id: str, result: RagResult
    ) -> None:
        """Persist this turn's policy chunks for the next turn's reuse check."""
        records = [
            RetrievedChunkRecord(
                content=c.content,
                document_id=c.document_id,
                chunk_index=c.chunk_index,
                org_id=c.org_id,
            )
            for c in result.sources
        ]
        self._memory.set_last_retrieval(conversation_id, org_id, records)

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """Cosine similarity in [-1, 1], matching pgvector's ``1 - cosine_distance``.

        Embeddings are already L2-normalized (BGE-M3), so this reduces to a dot
        product — but we compute the full form so a non-normalizing backend still
        yields a comparable score on the same scale as the confidence gate.
        """
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if denom == 0.0:
            return 0.0
        return float(np.dot(va, vb) / denom)

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

    def _update_running_summary(self, conversation_id: str) -> None:
        """Incrementally fold the turn(s) that just left the verbatim window into
        the running summary (Phase 8).

        Called after *every* turn. Once the number of verbatim turns exceeds the
        window, exactly one turn (the oldest) has fallen out; we merge just that
        turn with the existing summary — so each update's input is the summary plus
        a single turn, never the full history, and its cost stays ~constant no
        matter how long the conversation gets. (If a previous update was skipped on
        an LLM error, a small backlog is folded in on the next turn — still bounded,
        never the whole history.)
        """
        window = self._memory_settings.recent_turns
        turns = self._memory.get_turns(conversation_id)
        if len(turns) <= window:
            return  # window not full yet: everything is still kept verbatim

        falling_out = turns[:-window] if window > 0 else turns
        existing = self._memory.get_summary(conversation_id)
        prompt = build_summary_prompt(
            existing, [(t.question, t.answer) for t in falling_out]
        )
        try:
            summary = self._llm.generate(prompt).strip()
        except LLMProviderError:
            return  # best-effort; the turn stays verbatim and is folded in next time
        if summary:
            self._memory.set_summary_and_prune(conversation_id, summary, window)

    @staticmethod
    def _is_refusal(text: str, fallback_response: str) -> bool:
        """True if the model's reply is (essentially) the fixed refusal sentence.

        Robust to trailing punctuation / whitespace / case and to the model
        wrapping the sentence, so a refusal is never mistaken for a real answer.
        """
        core = fallback_response.rstrip(".").strip().lower()
        return core in text.strip().lower()
