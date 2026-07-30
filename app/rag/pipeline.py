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

Phase 15 moves the summary fold **off the critical path**: ``answer()`` schedules
``_update_running_summary`` on a background executor (see ``summary_fold.py``)
and returns the already-decided ``RagResult`` without waiting. The fold remains
best-effort. A per-conversation barrier runs before the next turn's rewrite so
a turn that left the verbatim window is never invisible to both the summary and
recent turns while a fold is still in flight.

Phase 17 adds a **non-LLM** corpus-vocab SymSpell pass on the retrieval
query (``query_normalize.py``) so first-time typo'd questions still rank
the right chunk — without paying an LLM rewrite on every request.

Bounded retrieval recovery addresses **Retrieval Discovery Gaps**: the first
retrieve runs exactly as today; only when available evidence looks insufficient
(gate miss, or generation finds the context insufficient) may one optional
recovery attempt produce alternative retrieval-oriented search expressions,
re-retrieve, and RRF-merge — then the same gate + grounded prompt apply again.
Recovery never answers the question, never replaces user intent, and never
reduces grounding guarantees. Expander failure degrades to the existing path.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field, replace

import numpy as np

from ..config.settings import (
    DecomposeSettings,
    MemorySettings,
    QueryCacheSettings,
    QueryNormSettings,
    RagSettings,
    RecoverySettings,
    RequestBudgetSettings,
    ReuseSettings,
    WebSearchSettings,
)
from ..core.exceptions import LLMProviderError, WebSearchError
from ..embeddings.base import EmbeddingProvider
from ..llm.base import LLMProvider
from ..llm.metering import AUX_LLM_STAGES, log_llm_call
from .query_signals import log_query_signal
from ..memory.base import ConversationContext, ConversationStore, RetrievedChunkRecord
from ..vectorstore.base import RetrievedChunk, VectorStore
from ..websearch.base import SearchResult, WebSearchProvider
from .retrieval import HybridRetriever, RetrievalResult
from .decompose import looks_compound, parse_sub_questions
from .query_cache import QueryAnswerCache
from .request_budget import RequestBudget
from .summary_fold import schedule_summary_fold, wait_for_conversation_fold
from .query_normalize import CorpusSpellNormalizer
from .prompts import (
    MODE_B_FORBIDDEN_PHRASES,
    WEB_SEARCH_TOOL,
    build_decompose_prompt,
    build_grounded_prompt,
    build_recovery_queries_prompt,
    build_rewrite_prompt,
    build_summary_prompt,
    build_web_answer_prompt,
    build_web_decision_prompt,
)

# Unmistakable banner so a web-sourced answer never blends with a policy answer.
WEB_ANSWER_LABEL = "🌐 From a web search (NOT your organization's policy documents):"

# Architectural recovery reasons (implementation of "evidence insufficient").
RECOVERY_REASON_GATE_MISS = "gate_miss"
RECOVERY_REASON_INSUFFICIENT_EVIDENCE = "insufficient_evidence"

_MAX_RECOVERY_QUERY_LEN = 200

# Parses the "MODE: A|B|C\n\n<answer>" tag the grounded prompt now requires
# (see prompts.py rule 5). Case-insensitive; tolerant of extra whitespace.
_MODE_TAG_RE = re.compile(r"^\s*MODE:\s*([ABC])\s*\n+(.*)", re.IGNORECASE | re.DOTALL)

def _tone_retry_addendum(mode: str) -> str:
    """Appended to the grounded prompt on the one bounded tone-compliance retry.

    Mode-agnostic (applies to a violating Mode A or Mode B answer) so the
    correction still names the mode the model actually declared, and asks it
    to keep declaring the same mode on the corrected answer.
    """
    return (
        f"\n\nIMPORTANT CORRECTION: your previous answer declared Mode {mode} "
        "but used forbidden meta-language about sources (e.g. naming 'the "
        "document(s)/doc(s)'/'handbook' directly, saying 'according to the "
        "document', or saying you cannot give a definitive answer). Rewrite it: "
        "state facts directly in a natural, conversational voice with no "
        "meta-language about sources, following ALL of the rules for Mode "
        f"{mode} above exactly. Still begin with 'MODE: {mode}'."
    )


def _parse_tagged_mode(raw: str) -> tuple[str | None, str]:
    """Split a ``MODE: A|B|C`` tag off the front of a generation, if present.

    Returns ``(mode, answer_text)``. If the model didn't include a parseable
    tag (e.g. any test/fake that returns plain text with no tag), returns
    ``(None, raw)`` unchanged — callers must treat a missing tag as "unknown",
    never as an error, so behavior degrades gracefully instead of breaking.
    """
    match = _MODE_TAG_RE.match(raw)
    if not match:
        return None, raw
    return match.group(1).upper(), match.group(2).strip()


def _violates_mode_b_tone(text: str) -> bool:
    """True if ``text`` uses any forbidden document-meta-language phrase.

    Despite the name (kept for historical/import continuity), this now checks
    the same phrase list used for BOTH Mode A and Mode B — a fully-supported
    answer that narrates "the document says X" is just as robotic as Mode B
    doing it. See ``MODE_B_FORBIDDEN_PHRASES`` in prompts.py.
    """
    low = text.lower()
    return any(phrase in low for phrase in MODE_B_FORBIDDEN_PHRASES)


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
    - Recovery diagnostics (Retrieval Discovery Gap): ``recovery_used``,
      ``recovery_reason``, ``recovery_queries``, ``retrieval_improved``,
      ``top_score_before``, ``top_score_after``, ``final_answer_source``,
      ``latency_ms``.
    - ``response_mode``  the ``A``/``B``/``C`` tag the model declared for this
      answer (``None`` if it didn't include a parseable tag — the pipeline
      degrades gracefully rather than failing the request). A diagnostic.
    - ``tone_retry_used``  ``True`` when a declared Mode A or B answer used
      forbidden meta-language and the pipeline retried the generation once with a
      corrective reminder (see ``RagPipeline._generate``). A diagnostic; never
      loops more than once.
    - ``question_decomposed`` / ``sub_questions`` — Phase 18 compound-question
      split before retrieval (diagnostics).
    """

    answer: str
    answered: bool
    source: str = "policy"
    sources: list[RetrievedChunk] = field(default_factory=list)
    top_score: float | None = None
    resolved_question: str | None = None
    retrieval_reused: bool = False
    recovery_used: bool = False
    recovery_reason: str | None = None
    recovery_queries: list[str] = field(default_factory=list)
    retrieval_improved: bool = False
    top_score_before: float | None = None
    top_score_after: float | None = None
    final_answer_source: str | None = None
    latency_ms: float | None = None
    response_mode: str | None = None
    tone_retry_used: bool = False
    question_decomposed: bool = False
    sub_questions: list[str] = field(default_factory=list)
    cache_hit: bool = False
    budget_exhausted: bool = False


@dataclass(frozen=True)
class _RecoveryAttempt:
    """Internal result of one bounded recovery attempt."""

    hits: list[RetrievedChunk]
    gate_score: float | None
    queries: list[str]


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
        recovery_settings: RecoverySettings | None = None,
        decompose_settings: DecomposeSettings | None = None,
        llm_aux: LLMProvider | None = None,
        budget_settings: RequestBudgetSettings | None = None,
        query_cache: QueryAnswerCache | None = None,
        query_norm: CorpusSpellNormalizer | None = None,
        query_norm_settings: QueryNormSettings | None = None,
    ) -> None:
        self._llm = llm
        self._llm_aux = llm_aux or llm
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
        # Bounded retrieval recovery (optional; at most one attempt per answer).
        self._recovery_settings = recovery_settings or RecoverySettings.from_env()
        self._decompose_settings = decompose_settings or DecomposeSettings.from_env()
        self._budget_settings = budget_settings or RequestBudgetSettings.from_env()
        self._query_cache = query_cache if query_cache is not None else QueryAnswerCache()
        # Phase 17: cheap corpus-vocab spelling before embed/retrieve.
        self._query_norm = query_norm or CorpusSpellNormalizer(
            query_norm_settings or QueryNormSettings.from_env()
        )
        # Phase 6: hybrid + reranking retriever. When None, fall back to plain
        # vector search (the Phase 3 behaviour), keeping this pipeline usable
        # without the retrieval upgrades.
        self._retriever = retriever

    def _provider_for_stage(self, stage: str) -> LLMProvider:
        return self._llm_aux if stage in AUX_LLM_STAGES else self._llm

    def _generate_text(
        self,
        stage: str,
        prompt: str,
        *,
        org_id: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        provider = self._provider_for_stage(stage)
        text = provider.generate(prompt)
        log_llm_call(stage, provider, org_id=org_id, conversation_id=conversation_id)
        return text

    @property
    def memory(self) -> ConversationStore | None:
        """The configured conversation store, or ``None`` if memory is off.

        Exposed (like ``PolicyAgent.pipeline``) so a caller that needs to
        create a conversation up front — e.g. the chat API's
        ``POST /chat/conversations`` — can reach it without duplicating
        construction logic.
        """
        return self._memory

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
            # Phase 15: wait for any in-flight fold so rewrite sees a consistent
            # window (turn that left verbatim is either still in turns, or in summary).
            wait_for_conversation_fold(conversation_id)
            context = self._memory.get_context(
                conversation_id, self._memory_settings.recent_turns
            )
            if not context.is_empty():
                resolved = self._rewrite_question(
                    question, context, org_id=org_id, conversation_id=conversation_id
                )

        if conversation_id is None:
            cached = self._query_cache.get(org_id, resolved)
            if cached is not None:
                out = replace(
                    cached,
                    resolved_question=resolved if resolved != question else None,
                )
                log_query_signal(out, org_id=org_id, conversation_id=conversation_id)
                return out

        budget = RequestBudget.from_settings(self._budget_settings)
        result = self._run(
            resolved,
            org_id,
            conversation_id=conversation_id,
            budget=budget,
        )

        if conversation_id is None and not result.cache_hit:
            self._query_cache.put(org_id, resolved, result)

        if conversation_id is not None and self._memory is not None:
            result = replace(result, resolved_question=resolved)
            self._memory.append_turn(conversation_id, question, result.answer)
            # Remember this turn's policy chunks so the next turn can try to reuse
            # them (web/fallback answers have no reusable policy chunks -> cleared).
            self._remember_retrieval(conversation_id, org_id, result)
            # Phase 15: pure bookkeeping — nothing in ``result`` depends on it.
            # Schedule after the answer is known so the caller is not blocked.
            cid = conversation_id
            schedule_summary_fold(cid, lambda: self._update_running_summary(cid))

        log_query_signal(result, org_id=org_id, conversation_id=conversation_id)
        return result

    def answer_stream(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        chunk_chars: int = 40,
    ) -> tuple[Iterator[str], RagResult]:
        """Answer, then hand back the text as a chunk iterator instead of one string.

        Why this does NOT stream token-by-token from the LLM: whether a given
        ``_generate`` call is even the FINAL one is only known after inspecting
        its output — a gate pass can still trigger recovery-then-regenerate if
        ``_generation_found_evidence_insufficient`` fires (see ``_run``), and a
        recovery-exhausted miss can still fall through to the web-search tool (a
        different call shape entirely, ``generate_with_tools``). A declared Mode
        B answer can also trigger the one bounded tone-compliance retry. Streaming
        tokens as they arrive from a call that might get thrown away and replaced
        would either leak a discarded draft to the caller or require buffering
        anyway — so there is no correctness win, only risk, in threading a
        streaming callback through the gate/recovery/tone-retry/web branches.

        Instead this runs the complete, UNCHANGED ``answer()`` — every gate,
        recovery, tone-compliance, and grounding decision is resolved exactly as
        it always is — and only then chunks the final, already-decided answer
        text for progressive delivery. From the caller's side (e.g. the CLI or
        an SSE endpoint) this still avoids showing the whole answer in one go;
        it just never displays a token that could later be discarded.
        """
        result = self.answer(question, org_id, conversation_id=conversation_id)

        def _chunks() -> Iterator[str]:
            text = result.answer
            for i in range(0, len(text), chunk_chars):
                yield text[i : i + chunk_chars]

        return _chunks(), result

    # -- retrieval / gate / generation / recovery --------------------------

    def _run(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        budget: RequestBudget | None = None,
    ) -> RagResult:
        """First retrieve as today; recover at most once if evidence is insufficient."""
        t0 = time.perf_counter()
        budget = budget or RequestBudget.from_settings(self._budget_settings)
        min_stage = self._budget_settings.min_stage_seconds
        budget_exhausted = False

        # Phase 17: correct OOV query tokens toward this org's chunk vocabulary
        # before embed/retrieve/keyword. Generation still uses ``question``
        # (conversation-resolved intent); only the retrieval key is normalized.
        retrieval_question = self._normalize_for_retrieval(question, org_id)
        query_vec = self._embedder.embed([retrieval_question])[0]

        sub_questions: list[str] = [retrieval_question]
        question_decomposed = False

        reused = self._try_reuse(retrieval_question, org_id, query_vec, conversation_id)
        if reused is not None:
            hits, top_score = reused.hits, reused.gate_score
            retrieval_reused = True
        else:
            retrieval_reused = False
            raw_subs, question_decomposed = self._maybe_decompose(
                question, org_id=org_id, conversation_id=conversation_id, budget=budget
            )
            if question_decomposed:
                sub_questions = [
                    self._normalize_for_retrieval(s, org_id) for s in raw_subs
                ]
            else:
                sub_questions = [retrieval_question]
            hits, top_score = self._retrieve_for_subquestions(
                org_id, question, sub_questions
            )

        top_score_before = top_score
        recovery_used = False
        recovery_reason: str | None = None
        recovery_queries: list[str] = []

        def _finalize(result: RagResult) -> RagResult:
            after = result.top_score if result.top_score is not None else top_score
            improved = False
            if recovery_used and after is not None:
                # None before (empty first pass) → any positive after counts as improved.
                before = 0.0 if top_score_before is None else top_score_before
                improved = after > before
            return replace(
                result,
                recovery_used=recovery_used,
                recovery_reason=recovery_reason,
                recovery_queries=list(recovery_queries),
                retrieval_improved=improved,
                top_score_before=top_score_before,
                top_score_after=after if recovery_used else top_score_before,
                final_answer_source=result.source,
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 1),
                question_decomposed=question_decomposed,
                sub_questions=list(sub_questions) if question_decomposed else [],
                budget_exhausted=budget_exhausted,
            )

        # Gate miss → optional recovery before web/fallback.
        if self._gate_miss(hits, top_score):
            if self._recovery_available(recovery_used) and budget.can_spend(min_stage):
                attempt = self._recover_once(
                    question,
                    org_id,
                    hits,
                    reason=RECOVERY_REASON_GATE_MISS,
                    budget=budget,
                    conversation_id=conversation_id,
                )
                recovery_used = True
                recovery_reason = RECOVERY_REASON_GATE_MISS
                recovery_queries = list(attempt.queries)
                hits, top_score = attempt.hits, attempt.gate_score
            elif self._recovery_available(recovery_used):
                budget_exhausted = True
            if self._gate_miss(hits, top_score):
                return _finalize(
                    self._gate_failed(
                        question,
                        hits=hits,
                        top_score=top_score,
                        budget=budget,
                        conversation_id=conversation_id,
                        org_id=org_id,
                    )
                )

        result = self._generate(
            question,
            hits,
            top_score,
            retrieval_reused=retrieval_reused,
            org_id=org_id,
            conversation_id=conversation_id,
            budget=budget,
        )

        # Generation found evidence insufficient → one recovery if not yet used.
        if self._generation_found_evidence_insufficient(
            result
        ) and self._recovery_available(recovery_used):
            if budget.can_spend(min_stage):
                attempt = self._recover_once(
                    question,
                    org_id,
                    hits,
                    reason=RECOVERY_REASON_INSUFFICIENT_EVIDENCE,
                    budget=budget,
                    conversation_id=conversation_id,
                )
            recovery_used = True
            recovery_reason = RECOVERY_REASON_INSUFFICIENT_EVIDENCE
            recovery_queries = list(attempt.queries)
            hits, top_score = attempt.hits, attempt.gate_score
            if self._gate_miss(hits, top_score):
                # Internal recovery exhausted; web (if enabled) then fallback.
                return _finalize(
                    self._gate_failed(
                        question,
                        hits=hits,
                        top_score=top_score,
                        budget=budget,
                        conversation_id=conversation_id,
                        org_id=org_id,
                    )
                )
            result = self._generate(
                question,
                hits,
                top_score,
                retrieval_reused=False,
                org_id=org_id,
                conversation_id=conversation_id,
                budget=budget,
            )
        elif not budget.can_spend(min_stage):
            budget_exhausted = True

        if self._generation_found_evidence_insufficient(result):
            return _finalize(
                self._gate_failed(
                    question,
                    hits=hits,
                    top_score=top_score,
                    budget=budget,
                    conversation_id=conversation_id,
                    org_id=org_id,
                )
            )

        return _finalize(result)

    def _gate_miss(
        self, hits: list[RetrievedChunk], top_score: float | None
    ) -> bool:
        return (
            not hits
            or top_score is None
            or top_score < self._settings.similarity_threshold
        )

    def _recovery_available(self, recovery_used: bool) -> bool:
        return self._recovery_settings.enabled and not recovery_used

    def _generation_found_evidence_insufficient(self, result: RagResult) -> bool:
        """True when the generation stage judges available evidence insufficient.

        Architectural trigger — not tied to a specific detector. Current
        implementation: the model emitted the fixed fallback (``_is_refusal``).
        """
        return (not result.answered) or self._is_refusal(
            result.answer, self._settings.fallback_response
        )

    def _retrieve_once(
        self, org_id: str, query_text: str, query_vec: list[float]
    ) -> tuple[list[RetrievedChunk], float | None]:
        if self._retriever is not None:
            retrieval = self._retriever.retrieve(org_id, query_text, query_vec)
            return retrieval.hits, retrieval.gate_score
        hits = self._store.query(org_id, query_vec, top_k=self._settings.top_k)
        top_score = hits[0].score if hits else None
        return hits, top_score

    def _maybe_decompose(
        self,
        question: str,
        *,
        org_id: str,
        conversation_id: str | None,
        budget: RequestBudget,
    ) -> tuple[list[str], bool]:
        if not self._decompose_settings.enabled or not looks_compound(question):
            return [question], False
        if not budget.can_spend(self._budget_settings.min_stage_seconds):
            return [question], False
        try:
            raw = self._generate_text(
                "decompose",
                build_decompose_prompt(question),
                org_id=org_id,
                conversation_id=conversation_id,
            ).strip()
            subs = parse_sub_questions(raw, original=question)
        except Exception:
            return [question], False
        if len(subs) <= 1:
            return [question], False
        return subs, True

    def _retrieve_for_subquestions(
        self, org_id: str, original_question: str, sub_questions: list[str]
    ) -> tuple[list[RetrievedChunk], float | None]:
        if len(sub_questions) == 1:
            vec = self._embedder.embed([sub_questions[0]])[0]
            return self._retrieve_once(org_id, sub_questions[0], vec)

        vectors = self._embedder.embed(sub_questions)
        primary_text, primary_vec = sub_questions[0], vectors[0]
        extra = list(zip(sub_questions[1:], vectors[1:]))

        if self._retriever is not None:
            retrieval = self._retriever.retrieve(
                org_id,
                primary_text,
                primary_vec,
                extra_queries=[(t, v) for t, v in extra],
                rerank_query=original_question,
            )
            return retrieval.hits, retrieval.gate_score

        merged: dict[tuple[str, int], RetrievedChunk] = {}
        for q_text, q_vec in zip(sub_questions, vectors):
            for hit in self._store.query(org_id, q_vec, top_k=self._settings.top_k):
                key = (hit.document_id, hit.chunk_index)
                prev = merged.get(key)
                if prev is None or hit.score > prev.score:
                    merged[key] = hit
        hits = sorted(merged.values(), key=lambda h: h.score, reverse=True)[
            : self._settings.top_k
        ]
        top_score = hits[0].score if hits else None
        return hits, top_score

    def _generate(
        self,
        question: str,
        hits: list[RetrievedChunk],
        top_score: float | None,
        *,
        retrieval_reused: bool,
        org_id: str | None = None,
        conversation_id: str | None = None,
        budget: RequestBudget | None = None,
    ) -> RagResult:
        prompt = build_grounded_prompt(
            question=question,
            contexts=[h.content for h in hits],
            fallback_response=self._settings.fallback_response,
        )
        raw = self._generate_text(
            "generate",
            prompt,
            org_id=org_id,
            conversation_id=conversation_id,
        ).strip()
        mode, text = _parse_tagged_mode(raw)

        tone_retry_used = False
        min_stage = self._budget_settings.min_stage_seconds
        if (
            mode in ("A", "B")
            and _violates_mode_b_tone(text)
            and budget is not None
            and budget.can_spend(min_stage)
        ):
            retry_raw = self._generate_text(
                "tone-retry",
                prompt + _tone_retry_addendum(mode),
                org_id=org_id,
                conversation_id=conversation_id,
            ).strip()
            tone_retry_used = True
            retry_mode, retry_text = _parse_tagged_mode(retry_raw)
            mode, text = retry_mode, retry_text

        answered = not self._is_refusal(text, self._settings.fallback_response)
        answer = text if answered else self._settings.fallback_response
        return RagResult(
            answer=answer,
            answered=answered,
            source="policy" if answered else "none",
            sources=hits,
            top_score=top_score,
            retrieval_reused=retrieval_reused,
            response_mode=mode,
            tone_retry_used=tone_retry_used,
        )

    def _recover_once(
        self,
        question: str,
        org_id: str,
        prior_hits: list[RetrievedChunk],
        *,
        reason: str,
        budget: RequestBudget,
        conversation_id: str | None = None,
    ) -> _RecoveryAttempt:
        """One bounded recovery: expand retrieval expressions → re-retrieve → fuse.

        On any expander/retrieve failure, returns the prior hits unchanged
        (graceful degradation — never fails the request).
        """
        del reason  # logged by caller via recovery_reason; kept for call-site clarity
        queries = self._expand_recovery_queries(
            question, prior_hits, org_id=org_id, conversation_id=conversation_id
        )
        if not queries:
            return _RecoveryAttempt(
                hits=list(prior_hits),
                gate_score=prior_hits[0].score if prior_hits else None,
                queries=[],
            )

        ranked_lists: list[list[RetrievedChunk]] = []
        if prior_hits:
            ranked_lists.append(list(prior_hits))

        try:
            vectors = self._embedder.embed(queries)
        except Exception:
            return _RecoveryAttempt(
                hits=list(prior_hits),
                gate_score=prior_hits[0].score if prior_hits else None,
                queries=queries,
            )

        for q_text, q_vec in zip(queries, vectors):
            try:
                hits, _ = self._retrieve_once(org_id, q_text, q_vec)
            except Exception:
                continue
            if hits:
                ranked_lists.append(hits)

        if not ranked_lists:
            return _RecoveryAttempt(hits=[], gate_score=None, queries=queries)

        if len(ranked_lists) == 1:
            fused = ranked_lists[0]
        else:
            fused = HybridRetriever._rrf_fuse(ranked_lists, k=60)

        # Gate signal = best cosine among fused candidates (RRF never overwrites .score).
        gate_score = max((c.score for c in fused), default=None)
        fused = fused[: self._settings.top_k]
        return _RecoveryAttempt(hits=fused, gate_score=gate_score, queries=queries)

    def _expand_recovery_queries(
        self,
        question: str,
        hits: list[RetrievedChunk],
        *,
        org_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[str]:
        """Ask the LLM for alternate retrieval expressions; never fails the request."""
        snippets = [h.content for h in hits[:3]]
        prompt = build_recovery_queries_prompt(question, snippets)
        try:
            raw = self._generate_text(
                "recovery-expand",
                prompt,
                org_id=org_id,
                conversation_id=conversation_id,
            )
        except LLMProviderError:
            return []
        except Exception:
            return []
        return self._parse_recovery_queries(raw, question)

    def _parse_recovery_queries(self, raw: str, original_question: str) -> list[str]:
        """Validate expander output into ≤ max_queries distinct search expressions."""
        if not raw or not raw.strip():
            return []
        original_norm = " ".join(original_question.lower().split())
        out: list[str] = []
        seen: set[str] = set()
        for line in raw.splitlines():
            line = line.strip().lstrip("-•*").strip()
            # Drop numbering like "1." / "1)"
            if len(line) >= 2 and line[0].isdigit() and line[1] in ".)":
                line = line[2:].strip()
            if not line or len(line) > _MAX_RECOVERY_QUERY_LEN:
                continue
            norm = " ".join(line.lower().split())
            if not norm or norm == original_norm or norm in seen:
                continue
            seen.add(norm)
            out.append(line)
            if len(out) >= self._recovery_settings.max_queries:
                break
        return out

    def _gate_failed(
        self,
        question: str,
        hits: list[RetrievedChunk],
        top_score: float | None,
        *,
        budget: RequestBudget,
        org_id: str | None = None,
        conversation_id: str | None = None,
    ) -> RagResult:
        """Internal evidence insufficient: try web search (if enabled), else fallback."""
        min_stage = self._budget_settings.min_stage_seconds
        if (
            self._web_search is not None
            and self._web_search_settings.enabled
            and budget.can_spend(min_stage * 2)
        ):
            web = self._try_web_search(
                question,
                top_score,
                org_id=org_id,
                conversation_id=conversation_id,
            )
            if web is not None:
                return web
        return RagResult(
            answer=self._settings.fallback_response,
            answered=False,
            source="none",
            sources=hits,
            top_score=top_score,
            budget_exhausted=not budget.can_spend(min_stage),
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

    def _try_web_search(
        self,
        question: str,
        top_score: float | None,
        *,
        org_id: str | None = None,
        conversation_id: str | None = None,
    ) -> RagResult | None:
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
            log_llm_call(
                "web-decision",
                self._llm,
                org_id=org_id,
                conversation_id=conversation_id,
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
            raw = self._generate_text(
                "web-answer",
                build_web_answer_prompt(question, results_block),
                org_id=org_id,
                conversation_id=conversation_id,
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

    def _normalize_for_retrieval(self, question: str, org_id: str) -> str:
        """Cheap corpus-vocab spelling fix for the retrieval key (Phase 17)."""
        if not self._query_norm.enabled:
            return question
        try:
            texts = self._store.list_chunk_texts(org_id)
        except NotImplementedError:
            return question
        except Exception:  # noqa: BLE001
            return question
        return self._query_norm.normalize(question, org_id, texts)

    # -- Capability A: conversation memory helpers -------------------------

    def _rewrite_question(
        self,
        question: str,
        context: ConversationContext,
        *,
        org_id: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        recent = [(t.question, t.answer) for t in context.recent_turns]
        prompt = build_rewrite_prompt(question, context.summary, recent)
        try:
            rewritten = self._generate_text(
                "rewrite",
                prompt,
                org_id=org_id,
                conversation_id=conversation_id,
            ).strip()
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
        the running summary (Phase 8 / 15).

        Scheduled in the background after *every* turn (Phase 15) — ``answer()``
        does not wait. Once the number of verbatim turns exceeds the window,
        exactly one turn (the oldest) has fallen out; we merge just that turn with
        the existing summary — so each update's input is the summary plus a single
        turn, never the full history, and its cost stays ~constant no matter how
        long the conversation gets. (If a previous update was skipped on an LLM
        error, a small backlog is folded in on the next turn — still bounded,
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
            summary = self._generate_text(
                "summary-fold",
                prompt,
                conversation_id=conversation_id,
            ).strip()
        except LLMProviderError:
            return  # best-effort; the turn stays verbatim and is folded in next time
        if summary:
            self._memory.set_summary_and_prune(conversation_id, summary, window)

    @staticmethod
    def _is_refusal(text: str, fallback_response: str) -> bool:
        """True if the model's reply is (essentially) the fixed refusal sentence.

        Robust to trailing punctuation / whitespace / case and to the model
        wrapping the sentence, so a refusal is never mistaken for a real answer.

        Current implementation of "generation found evidence insufficient" —
        not the architectural definition of that trigger.
        """
        core = fallback_response.rstrip(".").strip().lower()
        return core in text.strip().lower()
