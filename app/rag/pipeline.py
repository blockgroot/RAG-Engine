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

Phase 10 fixes a vocabulary-mismatch problem: a user's phrasing ("protein
supplements reimbursed") and a document's own wording ("health-related
products", "permissible expenses") often diverge, so a question with a real
answer could retrieve weak evidence and fall back needlessly. The normal path
is UNCHANGED and costs nothing extra — the fixes below are conditional, only
engaging when the normal path is already about to fail:
- **Bounded retrieval retry** (``_attempt_retrieval_retry``) — the FIRST
  retrieval attempt always uses the question exactly as given (or, in a
  conversation, exactly as Phase 5's rewrite already resolved it) — no
  normalization, no LLM call, identical to pre-Phase-10 behavior and zero added
  latency/cost on the common path. A retry engages ONLY when (a) the confidence
  gate fails, or (b) the gate passes but evidence classification (below) still
  comes back ``none``. On retry, ONE LLM call (``query_understanding.py``)
  proposes up to a couple of document-vocabulary-style alternate phrasings
  (fix typos/abbreviations/vague wording; never answers, never invents facts);
  retrieval re-runs including the ORIGINAL question plus those alternates
  (Phase 6's RRF fusion already generalizes to N ranked lists). Capped at ONE
  retry total, never recursive — if it still doesn't clear the gate or still
  classifies ``none``, the pipeline returns the ordinary fallback.
- **Evidence classification + graded generation** (``_generate_and_verify``) —
  the old strict "answer or emit the exact refusal" prompt is replaced by ONE
  call that both classifies how well the evidence supports the question
  (explicit / implicit / partial / none) and drafts a style-appropriate answer,
  so genuinely related-but-not-explicit evidence produces a clearly-labelled,
  honest answer instead of a blind fallback. Only ``none`` still returns the
  fixed fallback — the "none" bar (genuinely unrelated evidence) is unchanged
  from the old prompt. This still runs on every gate-passing question, exactly
  as the old strict-prompt call did — it replaces one LLM call with another,
  adding no extra round trip on the normal path.
- **Answer verification** (``app/verification/``) — deterministic, no LLM call:
  every sentence of the drafted answer is checked for semantic support in the
  retrieved evidence via the already-loaded embedding model. An unsupported claim
  triggers ONE stricter regeneration attempt; if still unsupported, the pipeline
  falls back rather than let an ungrounded claim through.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, replace

import numpy as np

from ..config.settings import MemorySettings, RagSettings, ReuseSettings, WebSearchSettings
from ..core.exceptions import LLMProviderError, WebSearchError
from ..core.telemetry import RetryTelemetry
from ..embeddings.base import EmbeddingProvider
from ..llm.base import LLMProvider
from ..memory.base import ConversationContext, ConversationStore, RetrievedChunkRecord
from ..vectorstore.base import RetrievedChunk, VectorStore
from ..verification.base import Verifier
from ..websearch.base import SearchResult, WebSearchProvider
from .query_understanding import QueryUnderstander
from .retrieval import HybridRetriever, RetrievalResult
from .prompts import (
    WEB_SEARCH_TOOL,
    build_classified_grounded_prompt,
    build_rewrite_prompt,
    build_stricter_regeneration_prompt,
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
    - ``evidence_classification``  (Phase 10) how well the retrieved evidence
      supported the question — ``"explicit"``, ``"implicit"``, ``"partial"``, or
      ``"none"``. ``None`` when the gate short-circuited before classification
      ran (nothing retrieved, or below the similarity threshold). This is an
      orthogonal diagnostic to ``source``: ``source`` stays ``"policy"`` for all
      three answered classifications (explicit/implicit/partial) — it is the
      branch signal everything else already depends on — while
      ``evidence_classification`` carries the finer distinction.
    """

    answer: str
    answered: bool
    source: str = "policy"
    sources: list[RetrievedChunk] = field(default_factory=list)
    top_score: float | None = None
    resolved_question: str | None = None
    retrieval_reused: bool = False
    evidence_classification: str | None = None


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
        query_understander: "QueryUnderstander | None" = None,
        verifier: Verifier | None = None,
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
        # Phase 10: query understanding/expansion + answer verification. Both
        # default to None, so any caller constructing RagPipeline directly
        # (unit tests, fakes) gets EXACTLY the pre-Phase-10 behaviour unless it
        # explicitly opts in — only build_rag_pipeline() wires them by default.
        self._query_understander = query_understander
        self._verifier = verifier

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
        # 1) Phase 8 retrieval reuse + first retrieval attempt — UNCHANGED from
        #    pre-Phase-10 behavior: the question is used EXACTLY as given (or, in
        #    a conversation, exactly as Phase 5's rewrite resolved it). No LLM
        #    call happens here, so the normal path costs nothing extra.
        query_vec = self._embedder.embed([question])[0]

        reused = self._try_reuse(question, org_id, query_vec, conversation_id)
        if reused is not None:
            hits, top_score = reused.hits, reused.gate_score
            retrieval_reused = True
        else:
            retrieval_reused = False
            if self._retriever is not None:
                retrieval = self._retriever.retrieve(org_id, question, query_vec)
                hits, top_score = retrieval.hits, retrieval.gate_score
            else:
                hits = self._store.query(org_id, query_vec, top_k=self._settings.top_k)
                top_score = hits[0].score if hits else None

        # Telemetry state (Phase 10, observability only — none of this is read by
        # any decision below; it only feeds the SINGLE emission at the very end
        # of this method, so exactly one record is emitted per _run() call).
        top_score_before_retry = top_score
        retried = False
        retry_trigger: str | None = None
        retry_latency_ms = 0.0
        generated_queries: list[str] = []
        retry_top_score: float | None = None
        retry_narrow_success = False  # did the retry achieve ITS OWN goal (gate cleared / verdict flipped)?

        # 2) Confidence gate (layer 1) — unchanged threshold logic. If it fails,
        #    try ONE bounded retrieval retry (Phase 10) before falling back: ask
        #    the LLM to improve the query for retrieval only, then re-retrieve
        #    including the ORIGINAL question plus its suggestions. This is the
        #    only way retrieval differs from pre-Phase-10 behavior, and it never
        #    fires when the plain retrieval already found strong-enough evidence.
        if not hits or top_score is None or top_score < self._settings.similarity_threshold:
            retry_trigger = "low_similarity"
            retry_start = time.perf_counter()
            retry = self._attempt_retrieval_retry(question, org_id)
            retry_latency_ms = (time.perf_counter() - retry_start) * 1000
            retried = retry is not None
            if retry is not None:
                retry_hits, retry_top_score, generated_queries = retry
                if (
                    retry_hits
                    and retry_top_score is not None
                    and retry_top_score >= self._settings.similarity_threshold
                ):
                    hits, top_score = retry_hits, retry_top_score
                    retry_narrow_success = True
                    # fall through to classification below — the FINAL source
                    # (retry vs. fallback) depends on what classification says.
                else:
                    self._emit_retry_telemetry(
                        question, retry_trigger, generated_queries, retried,
                        success=False, latency_ms=retry_latency_ms,
                        score_before=top_score_before_retry, score_after=retry_top_score,
                        source="fallback",
                    )
                    return self._gate_failed(
                        question, hits=retry_hits or [], top_score=retry_top_score
                    )
            else:
                self._emit_retry_telemetry(
                    question, retry_trigger, [], False,
                    success=False, latency_ms=retry_latency_ms,
                    score_before=top_score_before_retry, score_after=None,
                    source="fallback",
                )
                return self._gate_failed(question, hits=hits or [], top_score=top_score)

        # 3) Evidence classification + graded grounded generation (layer 2,
        #    Phase 10) — replaces the old binary strict-refuse prompt with
        #    explicit/implicit/partial/none. Runs once per gate-passing question,
        #    exactly as the old strict-prompt call did (one LLM call either way).
        result = self._generate_and_verify(question, hits, top_score, retrieval_reused)

        # 4) Second retry trigger: the gate passed, but the model still couldn't
        #    answer confidently ("none") from this evidence. This guardrail
        #    catches cases where cosine similarity alone doesn't reveal a
        #    vocabulary mismatch (the chunk "looks" related enough to clear 0.35
        #    but isn't the RIGHT chunk). Bounded to the SAME single retry budget
        #    as step 2 — never fires if a retry already happened.
        if result.evidence_classification == "none" and not retried:
            retry_trigger = "llm_fallback"
            retry_start = time.perf_counter()
            retry = self._attempt_retrieval_retry(question, org_id)
            retry_latency_ms = (time.perf_counter() - retry_start) * 1000
            retried = retry is not None
            if retry is not None:
                retry_hits, retry_top_score, generated_queries = retry
                if (
                    retry_hits
                    and retry_top_score is not None
                    and retry_top_score >= self._settings.similarity_threshold
                ):
                    retried_result = self._generate_and_verify(
                        question, retry_hits, retry_top_score, retrieval_reused=False
                    )
                    if retried_result.evidence_classification != "none":
                        retry_narrow_success = True
                        self._emit_retry_telemetry(
                            question, retry_trigger, generated_queries, retried,
                            success=True, latency_ms=retry_latency_ms,
                            score_before=top_score_before_retry, score_after=retry_top_score,
                            source="retry",
                        )
                        return retried_result
            # Retry (trigger 2) ran but did not flip the verdict — falls through
            # to the single emission below with source="fallback", returning the
            # ORIGINAL `result` unchanged (never the retry's own draft), exactly
            # as before this instrumentation was added.

        # Single emission point for every remaining path: no retry was ever
        # needed ("first_attempt"), a low_similarity retry succeeded and
        # classification then confirmed an answer ("retry"), or a retry ran but
        # the final verdict is still "none" ("fallback").
        if retry_trigger is None:
            source = "first_attempt"
        elif result.evidence_classification != "none":
            source = "retry"
        else:
            source = "fallback"

        self._emit_retry_telemetry(
            question, retry_trigger, generated_queries, retried,
            success=retry_narrow_success, latency_ms=retry_latency_ms,
            score_before=top_score_before_retry, score_after=retry_top_score,
            source=source,
        )
        return result

    @staticmethod
    def _emit_retry_telemetry(
        question: str,
        trigger: str | None,
        generated_queries: list[str],
        attempted: bool,
        *,
        success: bool,
        latency_ms: float,
        score_before: float | None,
        score_after: float | None,
        source: str,
    ) -> None:
        """Build and emit one structured retry-observability record (Phase 10).

        Pure observation — this cannot affect any decision the pipeline makes.
        See ``app/core/telemetry.py`` for the field definitions.
        """
        RetryTelemetry(
            retry_trigger=trigger,
            original_query=question,
            generated_retry_queries=generated_queries,
            retry_attempt_number=1 if attempted else 0,
            retry_success=success,
            retry_latency_ms=latency_ms,
            top_score_before_retry=score_before,
            top_score_after_retry=score_after,
            retrieval_improved=(
                attempted
                and score_before is not None
                and score_after is not None
                and score_after > score_before
            ),
            final_answer_source=source,
        ).emit()

    def _attempt_retrieval_retry(
        self, question: str, org_id: str
    ) -> tuple[list[RetrievedChunk], float | None, list[str]] | None:
        """ONE bounded retrieval retry (Phase 10) — never recursive.

        Asks the LLM to improve the query for RETRIEVAL ONLY (fix typos/
        abbreviations/vague wording, propose document-vocabulary-style
        alternates) and re-retrieves including the ORIGINAL question alongside
        up to a couple of its suggestions (``QueryUnderstandingSettings.
        max_expansions``, capped again here via ``all_queries(max_total=2)`` as
        a hard safety limit regardless of what the LLM returns). The LLM never
        answers the question and never invents facts — its only output is a
        short list of alternate search phrases.

        Returns ``None`` when no query-understanding capability is configured
        (nothing to retry with — the caller then just uses what it already
        has); otherwise ``(hits, gate_score, generated_queries)`` from
        re-retrieving with the expanded query set — ``generated_queries`` is the
        (possibly empty) list of NEW alternate phrasings actually tried, purely
        for observability (``app/core/telemetry.py``), and does not affect the
        retry's own decision-making. The retry may still fail the gate; the
        caller decides what to do with that.
        """
        if self._query_understander is None:
            return None

        understood = self._query_understander.understand(question)
        candidates = understood.all_queries(max_total=2)
        alt_texts = [
            t for t in candidates if t.strip().lower() != question.strip().lower()
        ]

        original_vec = self._embedder.embed([question])[0]
        if not alt_texts:
            # Nothing new to try (e.g. the LLM just echoed the question back).
            if self._retriever is not None:
                retrieval = self._retriever.retrieve(org_id, question, original_vec)
                return retrieval.hits, retrieval.gate_score, []
            hits = self._store.query(org_id, original_vec, top_k=self._settings.top_k)
            return hits, (hits[0].score if hits else None), []

        if self._retriever is not None:
            alt_vecs = self._embedder.embed(alt_texts)
            query_pairs = [(question, original_vec)] + list(zip(alt_texts, alt_vecs))
            retrieval = self._retriever.retrieve_expanded(org_id, question, query_pairs)
            return retrieval.hits, retrieval.gate_score, alt_texts

        # No hybrid retriever configured: fall back to a single richer query
        # (the LLM's normalized text) via plain vector search.
        hits = self._store.query(org_id, original_vec, top_k=self._settings.top_k)
        return hits, (hits[0].score if hits else None), alt_texts

    def _generate_and_verify(
        self,
        question: str,
        hits: list[RetrievedChunk],
        top_score: float,
        retrieval_reused: bool,
    ) -> RagResult:
        """Classify evidence support + draft an answer, then verify it (Phase 10).

        ONE LLM call classifies (explicit/implicit/partial/none) and drafts a
        style-matching answer; a genuinely unrelated "none" still returns the
        exact fixed fallback (identical bar to the old strict prompt). Any other
        classification passes through deterministic verification before reaching
        the user — an unsupported claim gets one stricter regeneration attempt,
        and only falls back if that retry is STILL unsupported.
        """
        evidence = [h.content for h in hits]
        prompt = build_classified_grounded_prompt(
            question=question,
            contexts=evidence,
            fallback_response=self._settings.fallback_response,
        )
        raw = self._llm.generate(prompt).strip()
        classification, drafted = self._parse_classified_response(raw)

        if classification == "none":
            return RagResult(
                answer=self._settings.fallback_response,
                answered=False,
                source="none",
                sources=hits,
                top_score=top_score,
                retrieval_reused=retrieval_reused,
                evidence_classification="none",
            )

        final_answer, final_classification = self._verify_and_maybe_regenerate(
            question, drafted, classification, evidence
        )
        if final_answer is None:
            return RagResult(
                answer=self._settings.fallback_response,
                answered=False,
                source="none",
                sources=hits,
                top_score=top_score,
                retrieval_reused=retrieval_reused,
                evidence_classification="none",
            )

        return RagResult(
            answer=final_answer,
            answered=True,
            source="policy",
            sources=hits,
            top_score=top_score,
            retrieval_reused=retrieval_reused,
            evidence_classification=final_classification,
        )

    # -- Phase 10: classification parsing + deterministic verification -----

    _CLASSIFICATION_RE = re.compile(
        r"CLASSIFICATION:\s*(explicit|implicit|partial|none)\b", re.IGNORECASE
    )
    _ANSWER_RE = re.compile(r"ANSWER:\s*(.*)", re.IGNORECASE | re.DOTALL)

    def _parse_classified_response(self, raw: str) -> tuple[str, str]:
        """Parse a classified-generation reply into ``(classification, answer)``.

        Falls back gracefully when the reply doesn't follow the
        ``CLASSIFICATION:``/``ANSWER:`` format (a model deviating from
        instructions, or a plain test fake that just returns fixed text): treats
        the whole reply as the answer, classified ``"none"`` if it IS
        (essentially) the fixed fallback sentence, else ``"explicit"`` — this
        preserves the old plain-answer behavior exactly for such replies.
        """
        cls_match = self._CLASSIFICATION_RE.search(raw)
        ans_match = self._ANSWER_RE.search(raw)
        if cls_match and ans_match:
            classification = cls_match.group(1).lower()
            if classification == "none":
                # The fallback sentence must be the exact canonical string
                # (gate/prompt/detection all agree on one string), regardless of
                # whatever free text the model produced after the label.
                return "none", self._settings.fallback_response
            return classification, ans_match.group(1).strip()

        if self._is_refusal(raw, self._settings.fallback_response):
            return "none", self._settings.fallback_response
        return "explicit", raw

    def _verify_and_maybe_regenerate(
        self, question: str, drafted: str, classification: str, evidence: list[str]
    ) -> tuple[str | None, str | None]:
        """Verify ``drafted``'s claims against ``evidence``; regenerate once if
        any are unsupported. Returns ``(None, None)`` if the retry is still
        unsupported (the caller then returns the fixed fallback) — an
        unsupported claim is never allowed through, per Phase 10's requirement.

        Deterministic verification (no LLM call) unless a regeneration is
        actually needed, so the common case (a well-supported draft) costs
        nothing extra beyond the classification/generation call already made.
        """
        if self._verifier is None:
            return drafted, classification

        result = self._verifier.verify(drafted, evidence)
        if result.supported:
            return drafted, classification

        retry_prompt = build_stricter_regeneration_prompt(
            question=question,
            contexts=evidence,
            fallback_response=self._settings.fallback_response,
            previous_answer=drafted,
            unsupported_sentences=result.unsupported,
        )
        try:
            raw2 = self._llm.generate(retry_prompt).strip()
        except LLMProviderError:
            return None, None

        classification2, drafted2 = self._parse_classified_response(raw2)
        if classification2 == "none":
            return None, None

        result2 = self._verifier.verify(drafted2, evidence)
        if result2.supported:
            return drafted2, classification2
        return None, None

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
