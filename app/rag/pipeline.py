"""RAG orchestration for grounded, org-scoped answers."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field, replace
from urllib.parse import urlparse

import numpy as np

from ..config.settings import (
    DEFAULT_MAX_CHUNK_CHARS,
    AttachmentSettings,
    AuditSettings,
    DecomposeSettings,
    GuardSettings,
    MemorySettings,
    QueryNormSettings,
    RagSettings,
    RecoverySettings,
    RequestBudgetSettings,
    ReuseSettings,
    SecuritySettings,
    ToneSettings,
    WebSearchSettings,
)
from ..core.answer_sources import (
    RECOVERY_REASON_GATE_MISS as _RECOVERY_REASON_GATE_MISS,
    RECOVERY_REASON_INSUFFICIENT_EVIDENCE as _RECOVERY_REASON_INSUFFICIENT_EVIDENCE,
    SOURCE_ATTACHMENT,
    SOURCE_NONE,
    SOURCE_POLICY,
    SOURCE_WEB,
)
from ..core.exceptions import LLMProviderError, WebSearchError
from ..core.streaming import chunk_answer
from ..embeddings.base import EmbeddingProvider
from ..llm.base import LLMProvider
from ..llm.metering import AUX_LLM_STAGES, log_llm_call
from ..llm.stages import (
    STAGE_AUDIT,
    STAGE_DECOMPOSE,
    STAGE_EMPATHY_OPENER,
    STAGE_GENERATE,
    STAGE_RECOVERY_EXPAND,
    STAGE_REWRITE,
    STAGE_SUMMARY_FOLD,
    STAGE_TONE_CLASSIFY,
    STAGE_TONE_RETRY,
    STAGE_WEB_ANSWER,
    STAGE_WEB_DECISION,
    USER_FACING_LLM_STAGES,
)
from ..llm.routed import default_model_only
from .query_signals import log_query_signal
from ..memory.base import ConversationContext, ConversationStore, RetrievedChunkRecord
from ..vectorstore.base import DateRange, RetrievedChunk, VectorStore, Viewer
from ..websearch.base import SearchResult, WebSearchProvider
from .attachment_tools import (
    READ_FILE_TOOL,
    AttachedFile,
    build_preview_block,
    run_reads,
)
from ..sources.slack_utils import channel_tag
from .access_notice import live_withheld_notice, restricted_notice
from ..guard import build_injection_guard
from ..livetools import LiveRefresh, current_live_request
from ..memory.personal import current_asker_context, current_asker_facts
from ..livetools import refresh as live_refresh
from ..livetools.handles import mint as mint_handles
from ..livetools.handles import resolve as resolve_handle
from ..livetools.handles import strip_handles
from ..guard.moderation import answer_is_unsafe
from ..security.links import enforce_link_provenance, strip_links
from ..security.outbound import user_worded_query
from ..security.untrusted import leaks_canary, scrub_untrusted_text
from .audit import lettuce_verdict, parse_audit_verdict
from .retrieval import HybridRetriever, RetrievalResult, gate_document
from .cite import link_citations, strip_citations
from .context_assemble import assemble_context_texts, describe_hit
from .decompose import looks_compound, parse_sub_questions
from .scope_intent import OVERVIEW, classify_scope_intent
from . import progress
from .deep import deep_read_on, extra_effort_on, reset_deep_read, use_deep_read  # noqa: F401 -- re-exported
from .request_budget import RequestBudget
from .summary_fold import schedule_summary_fold, wait_for_conversation_fold
from .query_normalize import CorpusSpellNormalizer
from .source_meta import uses_source_meta_language
from .question_tone import (
    QuestionTone,
    build_empathy_opener_prompt,
    build_question_tone_prompt,
    compose_supportive_answer,
    normalize_opener,
    parse_question_tone,
)
from .prompts import (
    POLICY_PROMPT_PROFILE,
    REFRESH_ITEM_TOOL,
    WEB_SEARCH_TOOL,
    PromptProfile,
    build_live_decision_prompt,
    build_audit_prompt,
    build_decompose_prompt,
    ATTACHMENT_PROMPT_PROFILE,
    connected_prompt_profile,
    build_attachment_paging_prompt,
    build_grounded_prompt,
    build_recovery_queries_prompt,
    build_rewrite_prompt,
    build_summary_prompt,
    build_web_answer_prompt,
    build_web_decision_prompt,
)

WEB_ANSWER_LABEL = "🌐 From a web search (NOT your organization's policy documents):"

RECOVERY_REASON_GATE_MISS = _RECOVERY_REASON_GATE_MISS
RECOVERY_REASON_INSUFFICIENT_EVIDENCE = _RECOVERY_REASON_INSUFFICIENT_EVIDENCE

#: How many best hits get their neighbouring chunks (RAG_NEIGHBOR_CHUNKS).
_NEIGHBOR_TOP_HITS = 2

_MAX_RECOVERY_QUERY_LEN = 200

_MODE_TAG_RE = re.compile(r"^\s*MODE:\s*([ABC])\s*\n+(.*)", re.IGNORECASE | re.DOTALL)
#: The tag on its own line after a short lead-in (Haiku sometimes writes one
#: sentence first). Bounded so a "MODE:" quoted deep in an answer is not the tag.
_LATE_MODE_TAG_RE = re.compile(r"^[ \t]*MODE:[ \t]*([ABC])[ \t]*\n+", re.IGNORECASE | re.MULTILINE)
_MODE_LEAD_IN_CHARS = 400



logger = logging.getLogger(__name__)

# Tone only adds an empathy opener, so it runs alongside generation, capped here.
_TONE_GRACE_SECONDS = 1.0
_AUX_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="rag-aux")


def _read_order(hit) -> tuple[float, str, int]:
    """Oldest first, stable within a document. A missing timestamp sorts first,
    never dropped."""
    when = getattr(hit, "last_modified", None)
    try:
        stamp = when.timestamp() if when is not None else 0.0
    except Exception:  # noqa: BLE001 - an undatable row still belongs in the read
        stamp = 0.0
    return (stamp, hit.document_id or "", hit.chunk_index or 0)


#: Heads the graph-facts block, so the model can say where a fact came from.
GRAPH_FACTS_HEADER = (
    "Known connections (from the company's own tools: who wrote, edited, "
    "commented on or links to what):"
)


#: Heads the search-coverage lines. Facts, not an instruction.
SEARCH_COVERAGE_HEADER = "Search coverage:"


def _graph_facts_block(org_id: str | None, routed: str | None) -> tuple[str | None, bool]:
    """``(context_block, graph_shaped)`` from the plan the chat edge built
    (never a second walk). ``graph_shaped`` is True when the plan changed
    what this answer could see."""
    try:
        from ..graph.plan import current_plan

        plan = current_plan()
        if plan is None or plan.org_id != org_id:
            return None, False
        lines = plan.facts(routed)
        # Coverage is stated only on a connected answer.
        searched = plan.coverage_lines() if plan.cross is not None else []
        shaped = bool(lines) or bool(searched) or plan.cross is not None
        if not lines and not searched:
            return None, shaped
        parts = []
        if lines:
            parts.append(GRAPH_FACTS_HEADER + "\n" + "\n".join(f"- {line}" for line in lines))
        if searched:
            parts.append(SEARCH_COVERAGE_HEADER + "\n" + "\n".join(f"- {line}" for line in searched))
        return "\n\n".join(parts), shaped
    except Exception:  # noqa: BLE001 - facts may only ever add
        logger.warning("graph facts skipped", exc_info=True)
        return None, False


#: Room ``describe_hit`` adds per block (title, app, editor, date).
_PROVENANCE_CHARS = 120

#: Most pieces a deep read takes from one document.
_DEEP_MAX_PIECES = 100


def _deep_spans(
    hits: list[RetrievedChunk],
    top: list[RetrievedChunk],
    n: int,
    top_docs: int,
    budget_chars: int,
) -> dict[str, list[int]]:
    """Which pieces of each top document a deep read takes, in reading order.

    Sized by the budget, and grown around EVERY hit in the document so a fact
    far from the best match is still read. Never fewer than 2n+1 pieces.
    """
    avg = max(1, sum(len(h.content) for h in hits) // len(hits))
    share = max(2 * n + 1, budget_chars // max(1, len(top)) // avg)
    if len(top) < top_docs:
        share = max(share, (2 * n + 1) * top_docs // len(top))
    # ponytail: a fixed cap on pieces per document so tiny pieces (Slack
    # messages) cannot ask for hundreds; the char budget still trims after.
    share = min(share, _DEEP_MAX_PIECES)
    spans: dict[str, list[int]] = {}
    for first in top:
        seeds = [h.chunk_index for h in hits if h.document_id == first.document_id]
        picked: set[int] = set()
        radius = 0
        while len(picked) < share and radius <= share:
            for i in seeds:
                for j in (i - radius, i + radius):
                    if j >= 0 and len(picked) < share:
                        picked.add(j)
            radius += 1
        spans[first.document_id] = sorted(picked)
    return spans


def _connected_tools(org_id: str | None) -> frozenset[str]:
    """The tools a connected answer is reading, or empty for a normal one."""
    try:
        from ..graph.plan import current_plan

        plan = current_plan()
        if plan is None or plan.cross is None or plan.org_id != org_id:
            return frozenset()
        return frozenset(plan.cross)
    except Exception:  # noqa: BLE001
        return frozenset()


def _tone_retry_addendum(mode: str) -> str:
    """Appended on the one bounded meta-language tone-compliance retry."""
    return (
        f"\n\nIMPORTANT CORRECTION: your previous answer declared Mode {mode} "
        "but used forbidden meta-language about sources (e.g. naming 'the "
        "document(s)/doc(s)'/'handbook' directly, saying 'according to the "
        "document', or saying you cannot give a definitive answer). Rewrite it: "
        "state facts directly in a natural, conversational voice with no "
        "meta-language about sources, following ALL of the rules for Mode "
        f"{mode} above exactly. Still begin with 'MODE: {mode}'."
    )


def _safe_json_args(raw: str | None) -> dict:
    """Tool-call arguments as a dict, never raising (a bad payload is skipped)."""
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_tagged_mode(raw: str) -> tuple[str | None, str]:
    """Split a ``MODE: A|B|C`` tag off a generation: ``(mode, answer_text)``.

    No parseable tag returns ``(None, raw)``; callers treat that as unknown,
    never an error.
    """
    match = _MODE_TAG_RE.match(raw)
    if match:
        return match.group(1).upper(), match.group(2).strip()
    late = _LATE_MODE_TAG_RE.search(raw, 0, _MODE_LEAD_IN_CHARS + 20)
    if late and late.start() <= _MODE_LEAD_IN_CHARS:
        # The answer is what follows the tag; the lead-in was narration.
        return late.group(1).upper(), raw[late.end():].strip()
    return None, raw


def _violates_mode_b_tone(text: str) -> bool:
    """True if ``text`` narrates sources instead of stating facts (Modes A and B).
    Detection is structural, not a phrase list."""
    return uses_source_meta_language(text)


@dataclass(frozen=True)
class RagResult:
    """The outcome of answering one question for one tenant.

    ``answered`` is False only for the fixed fallback; callers branch on it,
    never on the text. ``source`` is ``policy``, ``web`` or ``none``.
    ``sources`` all belong to the queried ``org_id``. The rest (scores,
    ``resolved_question``, ``retrieval_reused``, recovery, mode, tone, audit
    fields) are diagnostics; ``audit_downgraded`` is True only when the audit
    replaced the answer with the fallback.
    """

    answer: str
    answered: bool
    source: str = SOURCE_POLICY
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
    question_tone: str | None = None
    question_decomposed: bool = False
    sub_questions: list[str] = field(default_factory=list)
    budget_exhausted: bool = False
    audit_used: bool = False
    audit_downgraded: bool = False
    audit_reason: str | None = None
    # "Matched but not shared with you", carried as a flag so nothing matches on
    # text: a withheld document is not a documentation gap.
    access_restricted: bool = False
    # True when the graph plan shaped the answer.
    graph_shaped: bool = False
    # Connectors read LIVE for this answer: ``[{provider, fetched_at}]``.
    live_sources: list[dict] = field(default_factory=list)
    # True when a live read said the item behind the answer is gone or unreadable.
    # Not a documentation gap.
    live_withheld: bool = False
    # True when personal memory was in the prompt (answer is for ONE person).
    personalized: bool = False
    # ``[{n, document_id, title, provider, url}]`` per valid ``[n]`` (`rag/cite.py`).
    cited: list[dict] = field(default_factory=list)
    #: Retrieval found the answer spread over several documents (a wide read).
    wide_read: bool = False


def _without_withheld(
    hits: list[RetrievedChunk],
    top_score: float | None,
    gate_document_id: str | None,
    withheld: dict[str, str],
) -> tuple[list[RetrievedChunk], float | None]:
    """Drop withheld documents' chunks; re-check the gate only if it was theirs.

    The replacement score is a max over the remaining hits, so it can only be
    lower: an answer can become a refusal, never the reverse. A hit with no
    score is skipped, never trusted.
    """
    if not withheld:
        return hits, top_score
    kept = [h for h in hits if h.document_id not in withheld]
    if gate_document_id is None or gate_document_id in withheld:
        top_score = max((h.score for h in kept if h.score is not None), default=None)
    return kept, top_score


@dataclass(frozen=True)
class _RecoveryAttempt:
    """Internal result of one bounded recovery attempt."""

    hits: list[RetrievedChunk]
    gate_score: float | None
    queries: list[str]
    gate_document_id: str | None = None


def _screen_hits(hits, org_id: str | None, guard: GuardSettings) -> list:
    """Log every flagged chunk; in ``enforce`` mode also drop it.

    Dropped after retrieval, so the 0.35 gate score is untouched and the
    chunk's links fail provenance. Unscored (NULL) passes: a guard outage must
    not empty the corpus. Logs ids and score only, never chunk text.
    """
    if not guard.enabled:
        return hits
    kept = []
    for h in hits:
        if h.injection_score is not None and h.injection_score >= guard.threshold:
            logger.warning(
                "guard.flagged_hit mode=%s org=%s doc=%s chunk=%s score=%.3f",
                guard.mode, org_id, h.document_id, h.chunk_index, h.injection_score,
            )
            if guard.mode == "enforce":
                continue
        kept.append(h)
    return kept


class RagPipeline:
    """Composes embeddings + vector store + LLM into grounded, org-scoped Q&A.

    Build via ``factory.build_rag_pipeline``. ``memory`` and ``web_search``
    are optional: ``None`` turns that capability off.
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
        query_norm: CorpusSpellNormalizer | None = None,
        query_norm_settings: QueryNormSettings | None = None,
        prompt_profile: PromptProfile | None = None,
        tone_settings: ToneSettings | None = None,
        source_provider: str | None = None,
        audit_settings: AuditSettings | None = None,
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
        self._reuse_settings = reuse_settings or ReuseSettings.from_env()
        self._recovery_settings = recovery_settings or RecoverySettings.from_env()
        self._tone_settings = tone_settings or ToneSettings.from_env()
        self._decompose_settings = decompose_settings or DecomposeSettings.from_env()
        self._budget_settings = budget_settings or RequestBudgetSettings.from_env()
        self._query_norm = query_norm or CorpusSpellNormalizer(
            query_norm_settings or QueryNormSettings.from_env()
        )
        self._retriever = retriever
        self._source_provider = source_provider
        self._prompt_profile = prompt_profile or POLICY_PROMPT_PROFILE
        self._audit_settings = audit_settings or AuditSettings.from_env()
        self._link_allowlist = SecuritySettings.from_env().link_allowlist
        self._guard_settings = GuardSettings.from_env()
        self._injection_guard = build_injection_guard(self._guard_settings)

    def _provider_for_stage(self, stage: str) -> LLMProvider:
        return self._llm_aux if stage in AUX_LLM_STAGES else self._llm

    def _generate_text(
        self,
        stage: str,
        prompt: str,
        *,
        org_id: str | None = None,
        conversation_id: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        provider = self._provider_for_stage(stage)
        if stage in USER_FACING_LLM_STAGES:
            # The reader sees this text, so it uses the model they chose (or the default).
            text = provider.generate(prompt, max_tokens=max_tokens)
        else:
            # Machinery (rewrite, web decision, audit) is pinned to the configured model:
            # the grounding guarantees must not depend on a member's dropdown.
            with default_model_only():
                text = provider.generate(prompt, max_tokens=max_tokens)
        log_llm_call(stage, provider, org_id=org_id, conversation_id=conversation_id)
        return text

    @property
    def memory(self) -> ConversationStore | None:
        """The conversation store, or ``None`` if memory is off. Exposed so the
        chat API can create a conversation up front."""
        return self._memory

    @property
    def fallback_response(self) -> str:
        """The one fixed refusal string (see CLAUDE.md §4 — never a second copy)."""
        return self._settings.fallback_response

    def recent_chunks_for_recap(
        self,
        org_id: str,
        *,
        workspace_id: str | None = None,
        limit: int = 40,
        viewer: Viewer | None = None,
    ) -> list[RetrievedChunk]:
        """Recency-selected chunks from this pipeline's pinned corpus (Slack recap).

        Goes through the pipeline so ``source_provider`` stays pinned.
        """
        chunks = self._store.recent_chunks(
            org_id,
            self._source_provider,
            viewer=viewer,
            workspace_id=workspace_id,
            limit=limit,
        )
        # The recap builds its own prompt, so it screens here, not in _generate.
        return _screen_hits(chunks, org_id, self._guard_settings)

    def generate_raw(self, prompt: str) -> str:
        """One un-gated LLM call on this pipeline's provider.

        For a caller with its own already-fenced, scoped prompt (the Slack recap).
        No retrieval, gating or grounding: the caller owns those.
        """
        return self._llm.generate(prompt)

    # -- public API --------------------------------------------------------

    def answer(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
        attachments: list[tuple[str, str, bool]] | None = None,
        viewer: Viewer | None = None,
    ) -> RagResult:
        """Answer ``question`` from ``org_id``'s chunks only (plus optional memory).

        ``attachments`` (``(filename, text, truncated)``) join retrieval, never
        replace it. ``workspace_id`` scopes retrieval to that workspace ONLY;
        ``date_range`` filters on ``source_last_modified``. Neither changes the
        gate or the prompt. The store enforces the ``org_id`` filter.
        """
        resolved = question
        if conversation_id is not None and self._memory is not None:
            wait_for_conversation_fold(
                conversation_id,
                timeout=self._memory_settings.fold_wait_seconds,
            )
            context = self._memory.get_context(
                conversation_id, self._memory_settings.recent_turns
            )
            # Personal memory narrows the SEARCH too: with a remembered office the
            # question is rewritten even on a first turn.
            if not context.is_empty() or current_asker_context():
                resolved = self._rewrite_question(
                    question, context, org_id=org_id, conversation_id=conversation_id
                )

        budget = RequestBudget.from_settings(self._budget_settings)
        result = self._run(
            resolved,
            org_id,
            conversation_id=conversation_id,
            budget=budget,
            workspace_id=workspace_id,
            date_range=date_range,
            tags=tags,
            user_question=question,
            attachments=attachments,
            viewer=viewer,
        )

        if conversation_id is not None and self._memory is not None:
            result = replace(result, resolved_question=resolved)
            self._memory.append_turn(
                conversation_id, question, result.answer, result.cited or None
            )
            self._remember_retrieval(conversation_id, org_id, result)
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
        workspace_id: str | None = None,
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
        viewer: Viewer | None = None,
    ) -> tuple[Iterator[str], RagResult]:
        """Answer first, then stream the already-final text in chunks."""
        result = self.answer(
            question,
            org_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            date_range=date_range,
            tags=tags,
            viewer=viewer,
        )

        return chunk_answer(result.answer, chunk_chars), result

    # -- retrieval / gate / generation / recovery --------------------------

    def _run(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        budget: RequestBudget | None = None,
        workspace_id: str | None = None,
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
        user_question: str | None = None,
        attachments: list[tuple[str, str, bool]] | None = None,
        viewer: Viewer | None = None,
    ) -> RagResult:
        """Retrieve; recover at most once if evidence is insufficient.

        Attachments do not change retrieval or the 0.35 gate; they join the
        prompt. On a gate miss with files attached, the answer comes from the
        files instead of a refusal: an upload needs no similarity score.
        """
        t0 = time.perf_counter()
        budget = budget or RequestBudget.from_settings(self._budget_settings)
        min_stage = self._budget_settings.min_stage_seconds
        budget_exhausted = False
        tone_question = user_question or question

        attachment_contexts = (
            self.attachment_contexts(
                question,
                attachments,
                org_id=org_id,
                conversation_id=conversation_id,
            )
            if attachments
            else []
        )

        retrieval_question = self._normalize_for_retrieval(question, org_id)
        query_vec = self._embedder.embed([retrieval_question])[0]

        sub_questions: list[str] = [retrieval_question]
        question_decomposed = False
        recovery_queries: list[str] = []

        from ..insights.table_answers import current_answers

        # A turn with worked figures never reuses the last turn's passages.
        reused = (
            None
            if date_range is not None or tags is not None or current_answers()
            else self._try_reuse(retrieval_question, org_id, query_vec, conversation_id)
        )
        if reused is not None:
            hits, top_score = reused.hits, reused.gate_score
            gate_doc = reused.gate_document_id
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
            search_queries = sub_questions
            if (
                not question_decomposed
                and self._recovery_settings.proactive
                and budget.can_spend(min_stage)
            ):
                # Alternative wordings add candidates; the pool is reranked against the
                # ORIGINAL question.
                recovery_queries = self._expand_recovery_queries(
                    question, [], org_id=org_id, conversation_id=conversation_id
                )
                search_queries = [retrieval_question, *recovery_queries]
            hits, top_score, gate_doc = self._retrieve_for_subquestions(
                org_id,
                question,
                search_queries,
                workspace_id=workspace_id,
                date_range=date_range,
                tags=tags,
                viewer=viewer,
                known_vectors={retrieval_question: query_vec},
            )

        top_score_before = top_score
        recovery_used = False
        recovery_reason: str | None = None
        audit_used = False
        audit_downgraded = False
        audit_reason: str | None = None

        def _finalize(result: RagResult) -> RagResult:
            after = result.top_score if result.top_score is not None else top_score
            improved = False
            if recovery_used and after is not None:
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
                audit_used=audit_used,
                audit_downgraded=audit_downgraded,
                audit_reason=audit_reason,
                # Mode B (refusal path) sets its own; mode A's are added here.
                live_sources=result.live_sources or (
                    live.sources if result.answered and result.source != SOURCE_WEB else []
                ),
            )

        live = LiveRefresh()
        if self._gate_miss(hits, top_score):
            if self._recovery_available(recovery_used) and budget.can_spend(min_stage):
                attempt = self._recover_once(
                    question,
                    org_id,
                    hits,
                    reason=RECOVERY_REASON_GATE_MISS,
                    budget=budget,
                    conversation_id=conversation_id,
                    workspace_id=workspace_id,
                    date_range=date_range,
                    tags=tags,
                    viewer=viewer,
                )
                recovery_used = True
                recovery_reason = RECOVERY_REASON_GATE_MISS
                recovery_queries = list(attempt.queries)
                hits, top_score = attempt.hits, attempt.gate_score
                gate_doc = attempt.gate_document_id
            elif self._recovery_available(recovery_used):
                budget_exhausted = True
            if self._gate_miss(hits, top_score):
                # Nothing in the corpus, but the asker attached a document: answer from it.
                if attachment_contexts:
                    return _finalize(
                        self._answer_from_contexts(
                            question,
                            attachment_contexts,
                            org_id=org_id,
                            conversation_id=conversation_id,
                            budget=budget,
                            user_question=tone_question,
                        )
                    )
                return _finalize(
                    self._gate_failed(
                        question,
                        hits=hits,
                        top_score=top_score,
                        budget=budget,
                        conversation_id=conversation_id,
                        org_id=org_id,
                        workspace_id=workspace_id,
                        viewer=viewer,
                        query_vec=query_vec,
                        user_question=tone_question,
                    )
                )

        # Live re-read of what retrieval found. Runs AFTER the gate, so it only
        # freshens or withholds what the gate admitted; off => no change.
        live = live_refresh(hits, current_live_request(), guard_settings=self._guard_settings)
        if live.withheld:
            hits, top_score = _without_withheld(hits, top_score, gate_doc, live.withheld)
            if self._gate_miss(hits, top_score):
                return _finalize(
                    self._gate_failed(
                        question,
                        hits=hits,
                        top_score=top_score,
                        budget=budget,
                        conversation_id=conversation_id,
                        org_id=org_id,
                        workspace_id=workspace_id,
                        viewer=viewer,
                        query_vec=query_vec,
                        user_question=tone_question,
                        live_withheld=next(iter(live.withheld.values())),
                    )
                )

        wide_read = self._settings.top_k < len(hits) <= self._read_settings().ranked_max_hits
        result = self._generate(
            question,
            # Reused hits already carry their neighbours.
            hits if retrieval_reused else self._with_neighbors(
                hits, org_id, workspace_id=workspace_id, viewer=viewer
            ),
            top_score,
            retrieval_reused=retrieval_reused,
            org_id=org_id,
            conversation_id=conversation_id,
            budget=budget,
            user_question=tone_question,
            # Live blocks lead: they are the current state of the items below. In
            # `contexts`, so the audit and the link rule see them.
            extra_contexts=live.blocks + attachment_contexts,
            extra_blocks=live.cite_blocks + [None] * len(attachment_contexts),
            superseded=live.refreshed,
        )
        result = replace(result, wide_read=wide_read)
        audit_used, audit_downgraded, audit_reason = (
            result.audit_used,
            result.audit_downgraded,
            result.audit_reason,
        )

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
                    workspace_id=workspace_id,
                    date_range=date_range,
                    tags=tags,
                    viewer=viewer,
                )
            recovery_used = True
            recovery_reason = RECOVERY_REASON_INSUFFICIENT_EVIDENCE
            recovery_queries = list(attempt.queries)
            hits, top_score = attempt.hits, attempt.gate_score
            gate_doc = attempt.gate_document_id
            hits, top_score = _without_withheld(hits, top_score, gate_doc, live.withheld)
            if self._gate_miss(hits, top_score):
                # Nothing in the corpus, but the asker attached a document: answer from it.
                if attachment_contexts:
                    return _finalize(
                        self._answer_from_contexts(
                            question,
                            attachment_contexts,
                            org_id=org_id,
                            conversation_id=conversation_id,
                            budget=budget,
                            user_question=tone_question,
                        )
                    )
                return _finalize(
                    self._gate_failed(
                        question,
                        hits=hits,
                        top_score=top_score,
                        budget=budget,
                        conversation_id=conversation_id,
                        org_id=org_id,
                        workspace_id=workspace_id,
                        viewer=viewer,
                        query_vec=query_vec,
                        user_question=tone_question,
                    )
                )
            result = self._generate(
                question,
                self._with_neighbors(
                    hits, org_id, workspace_id=workspace_id, viewer=viewer
                ),
                top_score,
                retrieval_reused=False,
                org_id=org_id,
                conversation_id=conversation_id,
                budget=budget,
                user_question=tone_question,
                extra_contexts=live.blocks + attachment_contexts,
                extra_blocks=live.cite_blocks + [None] * len(attachment_contexts),
                superseded=live.refreshed,
            )
            audit_used, audit_downgraded, audit_reason = (
                result.audit_used,
                result.audit_downgraded,
                result.audit_reason,
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
                    workspace_id=workspace_id,
                    viewer=viewer,
                    query_vec=query_vec,
                    user_question=tone_question,
                )
            )

        return _finalize(result)

    def _read_settings(self) -> RagSettings:
        """How much to read: the deep read in Deep analysis mode, else as configured."""
        return self._settings.deep(extra=extra_effort_on()) if deep_read_on() else self._settings

    def _with_neighbors(
        self,
        hits: list[RetrievedChunk],
        org_id: str,
        *,
        workspace_id: str | None,
        viewer: Viewer | None,
    ) -> list[RetrievedChunk]:
        """The best hits with the chunks either side joined into their block.

        One block per hit, so ``[n]`` still points at a retrieved document. Runs
        after the gate and never changes a score. Neighbours pass the same org,
        workspace, viewer and injection filters. Any failure returns ``hits``.
        """
        rag = self._read_settings()
        n = rag.neighbor_chunks
        if n <= 0 or not hits or len(hits) > rag.ranked_max_hits:
            return hits
        top_docs = rag.neighbor_top_docs
        if top_docs > 0:
            # Depth goes to the best hit of each of the top N documents, never twice
            # into one.
            firsts: dict[str, RetrievedChunk] = {}
            for h in hits:
                if len(firsts) >= top_docs:
                    break
                firsts.setdefault(h.document_id, h)
            top = list(firsts.values())
            spans = _deep_spans(hits, top, n, top_docs, rag.context_chars_for(len(hits)))
        else:
            top = hits[:_NEIGHBOR_TOP_HITS]

        def window(h: RetrievedChunk):
            if top_docs <= 0:
                return range(max(0, h.chunk_index - n), h.chunk_index + n + 1)
            return spans[h.document_id]

        taken = {(h.document_id, h.chunk_index) for h in top}
        wanted = [
            (h.document_id, i) for h in top for i in window(h) if i != h.chunk_index
        ]
        try:
            found = self._store.chunks_at(
                org_id, wanted, workspace_id=workspace_id, viewer=viewer
            )
        except Exception:  # noqa: BLE001 - an extra read must never cost an answer
            logger.warning("neighbour read skipped", exc_info=True)
            return hits
        by_key = {
            (c.document_id, c.chunk_index): c
            for c in _screen_hits(found, org_id, self._guard_settings)
        }
        # Neighbours only fill room left after every selected hit's own piece, so
        # they never push another document out of the budget. Breadth first.
        hit_keys = {(h.document_id, h.chunk_index) for h in hits}
        room = (
            rag.context_chars_for(len(hits))
            - sum(len(h.content) + _PROVENANCE_CHARS for h in hits)
            if top_docs <= 0 or (extra_effort_on() and len(hits) > rag.top_k) else None
        )
        joined: list[RetrievedChunk] = []
        for h in top:
            parts = []
            for i in window(h):
                key = (h.document_id, i)
                if i == h.chunk_index:
                    parts.append(h.content)
                elif key in by_key and key not in taken:
                    extra = 0 if key in hit_keys else len(by_key[key].content)
                    if room is not None and extra > room:
                        continue
                    if room is not None:
                        room -= extra
                    taken.add(key)
                    parts.append(by_key[key].content)
            joined.append(replace(h, content="\n".join(parts)))
        top_keys = {(h.document_id, h.chunk_index) for h in top}
        rest = [h for h in hits if (h.document_id, h.chunk_index) not in taken
                and (h.document_id, h.chunk_index) not in top_keys]
        return joined + rest

    def retrieve_passages(
        self,
        question: str,
        org_id: str,
        *,
        about: str | None = None,
        workspace_id: str | None = None,
        tags: list[str] | None = None,
        viewer: Viewer | None = None,
    ) -> list[RetrievedChunk]:
        """Ask's retrieval without an answer (for Act): spelling fix, hybrid search,
        rerank, and the 0.35 gate. ``[]`` on a gate miss.

        ``about`` (the words naming the content) is searched alongside and orders
        the rerank, so action words do not outrank the content. With the deep read
        on, the best documents come with their neighbours.
        """
        query = self._normalize_for_retrieval(question, org_id)
        vec = self._embedder.embed([query])[0]
        if about and self._retriever is not None:
            about_q = self._normalize_for_retrieval(about, org_id)
            result = self._retriever.retrieve(
                org_id, query, vec,
                extra_queries=[(about_q, self._embedder.embed([about_q])[0])],
                rerank_query=about_q,
                workspace_id=workspace_id, tags=tags, viewer=viewer,
            )
            hits, gate = result.hits, result.gate_score
        else:
            hits, gate, _ = self._retrieve_once(
                org_id, query, vec, workspace_id=workspace_id, tags=tags, viewer=viewer)
        if self._gate_miss(hits, gate):
            return []
        return self._with_neighbors(hits, org_id, workspace_id=workspace_id, viewer=viewer) \
            if deep_read_on() else hits

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
        """True when generation judged the evidence insufficient (currently: the
        model emitted the fixed fallback)."""
        return (not result.answered) or self._is_refusal(
            result.answer, self._settings.fallback_response
        )

    def _whole_scope(
        self,
        org_id: str,
        query_text: str,
        query_vec: list[float],
        *,
        workspace_id: str | None,
        tags: list[str] | None,
        viewer: Viewer | None,
    ) -> tuple[list[RetrievedChunk], float | None] | None:
        """Breadth retrieval for a question about the corpus AS A WHOLE.

        Driven by the question (`scope_intent.classify_scope_intent`), not the
        data: "summarise the channel" and "what did Sana say about X" differ.
        ``None`` means "rank instead"; every failure returns it.
        """
        max_chunks = self._settings.scope_whole_max_chunks
        if max_chunks <= 0:
            return None
        # Only a SCOPED corpus: "all of it" company-wide is neither affordable nor meant.
        if not tags and workspace_id is None:
            return None

        # Classified before fetching: the read is the expensive half.
        try:
            intent = classify_scope_intent(query_text, llm=self._llm)
        except Exception:  # noqa: BLE001 - a classifier must never cost an answer
            logger.warning("scope intent failed; ranking instead", exc_info=True)
            return None
        if intent != OVERVIEW:
            return None

        try:
            hits = self._store.query(
                org_id,
                query_vec,
                # One over the bound detects "more than we can read" without a COUNT.
                top_k=max_chunks + 1,
                workspace_id=workspace_id,
                source_provider=self._source_provider,
                tags=tags,
                viewer=viewer,
            )
        except Exception:  # noqa: BLE001 - never lose an answer to a wider read
            logger.warning("breadth retrieval failed; ranking instead", exc_info=True)
            return None
        if not hits:
            return None

        # As much as a prompt holds: selected newest-first, read oldest-first.
        by_recency = sorted(hits, key=_read_order, reverse=True)
        kept: list[RetrievedChunk] = []
        used = 0
        for hit in by_recency:
            size = len(hit.content or "")
            if len(kept) >= max_chunks or used + size > self._settings.scope_whole_max_chars:
                break
            kept.append(hit)
            used += size
        if not kept:
            return None
        if len(kept) < len(hits):
            # Partial, and said so in the context: the model cannot disclose a cut it
            # was not told about.
            logger.info(
                "Breadth read covered %s of %s chunks in scope (%s chars)",
                len(kept), len(hits), used,
            )
        # The gate is unchanged: the best cosine in the retrieval.
        gate_score = max((h.score for h in kept), default=None)
        return sorted(kept, key=_read_order), gate_score

    def _retrieve_once(
        self,
        org_id: str,
        query_text: str,
        query_vec: list[float],
        *,
        workspace_id: str | None = None,
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
        viewer: Viewer | None = None,
    ) -> tuple[list[RetrievedChunk], float | None, str | None]:
        """``(hits, gate_score, gate_document_id)`` for one query."""
        whole = self._whole_scope(
            org_id, query_text, query_vec,
            workspace_id=workspace_id, tags=tags, viewer=viewer,
        )
        if whole is not None:
            # A whole read's gate is the max over exactly these hits.
            return whole[0], whole[1], gate_document(whole[0])
        if self._retriever is not None:
            retrieval = self._retriever.retrieve(
                org_id,
                query_text,
                query_vec,
                workspace_id=workspace_id,
                date_range=date_range,
                tags=tags,
                viewer=viewer,
            )
            return retrieval.hits, retrieval.gate_score, retrieval.gate_document_id
        hits = self._store.query(
            org_id,
            query_vec,
            top_k=self._settings.top_k,
            workspace_id=workspace_id,
            source_provider=self._source_provider,
            date_range=date_range,
            tags=tags,
            viewer=viewer,
        )
        top_score = hits[0].score if hits else None
        return hits, top_score, gate_document(hits[:1])

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
                STAGE_DECOMPOSE,
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
        self,
        org_id: str,
        original_question: str,
        sub_questions: list[str],
        *,
        workspace_id: str | None = None,
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
        viewer: Viewer | None = None,
        known_vectors: dict[str, list[float]] | None = None,
    ) -> tuple[list[RetrievedChunk], float | None, str | None]:
        """Retrieve for one or more sub-questions: ``(hits, gate, gate_document_id)``."""
        known = known_vectors or {}

        if len(sub_questions) == 1:
            only = sub_questions[0]
            vec = known.get(only)
            if vec is None:
                vec = self._embedder.embed([only])[0]
            return self._retrieve_once(
                org_id,
                only,
                vec,
                workspace_id=workspace_id,
                date_range=date_range,
                tags=tags,
                viewer=viewer,
            )

        missing = [s for s in sub_questions if s not in known]
        if missing:
            fresh = self._embedder.embed(missing)
            known = {**known, **dict(zip(missing, fresh))}
        vectors = [known[s] for s in sub_questions]
        primary_text, primary_vec = sub_questions[0], vectors[0]
        extra = list(zip(sub_questions[1:], vectors[1:]))

        if self._retriever is not None:
            retrieval = self._retriever.retrieve(
                org_id,
                primary_text,
                primary_vec,
                extra_queries=[(t, v) for t, v in extra],
                rerank_query=original_question,
                workspace_id=workspace_id,
                date_range=date_range,
                tags=tags,
                viewer=viewer,
            )
            return retrieval.hits, retrieval.gate_score, retrieval.gate_document_id

        merged: dict[tuple[str, int], RetrievedChunk] = {}
        for q_text, q_vec in zip(sub_questions, vectors):
            for hit in self._store.query(
                org_id,
                q_vec,
                top_k=self._settings.top_k,
                workspace_id=workspace_id,
                source_provider=self._source_provider,
                date_range=date_range,
                tags=tags,
                viewer=viewer,
            ):
                key = (hit.document_id, hit.chunk_index)
                prev = merged.get(key)
                if prev is None or hit.score > prev.score:
                    merged[key] = hit
        hits = sorted(merged.values(), key=lambda h: h.score, reverse=True)[
            : self._settings.top_k
        ]
        top_score = hits[0].score if hits else None
        return hits, top_score, gate_document(hits[:1])



    def _classify_question_tone(
        self,
        question: str,
        *,
        org_id: str | None = None,
        conversation_id: str | None = None,
        budget: RequestBudget | None = None,
    ) -> QuestionTone | None:
        """Aux-LLM intent: factual policy ask vs personal supportive ask."""
        if not self._tone_settings.enabled:
            return None
        min_stage = self._budget_settings.min_stage_seconds
        if budget is not None and not budget.can_spend(min_stage):
            return None
        try:
            raw = self._generate_text(
                STAGE_TONE_CLASSIFY,
                build_question_tone_prompt(question),
                org_id=org_id,
                conversation_id=conversation_id,
                max_tokens=16,
            )
        except Exception:
            return None
        return parse_question_tone(raw)

    def _empathy_opener(
        self,
        question: str,
        *,
        org_id: str | None = None,
        conversation_id: str | None = None,
        budget: RequestBudget | None = None,
    ) -> str | None:
        """One-sentence acknowledgment for SUPPORTIVE asks (aux LLM). Attempted even
        on a tight budget."""
        try:
            raw = self._generate_text(
                STAGE_EMPATHY_OPENER,
                build_empathy_opener_prompt(question),
                org_id=org_id,
                conversation_id=conversation_id,
                max_tokens=60,
            )
        except Exception:
            return None
        return normalize_opener(raw)

    def _answer_from_contexts(
        self,
        question: str,
        contexts: list[str],
        *,
        org_id: str | None,
        conversation_id: str | None,
        budget: RequestBudget | None,
        user_question: str | None,
    ) -> RagResult:
        """Generate from attachment contexts alone, labelled as such.

        Same strict prompt and audit; only the gate is absent (no cosine exists
        for an unembedded file).
        """
        result = self._generate(
            question,
            [],
            None,
            retrieval_reused=False,
            org_id=org_id,
            conversation_id=conversation_id,
            budget=budget,
            user_question=user_question,
            contexts=contexts,
            profile=ATTACHMENT_PROMPT_PROFILE,
        )
        return replace(
            result, source=SOURCE_ATTACHMENT if result.answered else SOURCE_NONE
        )

    def attachment_contexts(
        self,
        question: str,
        attachments: list[tuple[str, str, bool]],
        *,
        settings: AttachmentSettings | None = None,
        org_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[str]:
        """Prompt-ready context blocks for the attached files.

        Shared by the attachment-only and blended paths so they agree on when a
        file is too long to inline.
        """
        files = [
            AttachedFile(name, text, cut)
            for name, text, cut in attachments
            if text.strip()
        ]
        if not files:
            return []

        settings = settings or AttachmentSettings.from_env()
        if deep_read_on():
            # Deep analysis reads uploads whole up to the deep budget, and picks more
            # sections of a long one.
            settings = replace(
                settings,
                inline_char_budget=max(settings.inline_char_budget, self._settings.deep_max_context_chars),
                max_reads=max(settings.max_reads, settings.deep_max_reads),
            )
        progress.report(f"Reading {', '.join(f.filename for f in files)}")
        if sum(len(f.text) for f in files) <= settings.inline_char_budget:
            return [
                # The filename rides in the context so "which file says that?" is answerable.
                f"Attached file: {f.filename}"
                + (" (truncated — only the first part is shown)" if f.truncated else "")
                + "\n\n"
                + f.text
                for f in files
            ]
        return self._page_attachments(
            question,
            files,
            settings,
            org_id=org_id,
            conversation_id=conversation_id,
        )

    def answer_from_attachments(
        self,
        question: str,
        attachments: list[tuple[str, str, bool]],
        *,
        org_id: str | None = None,
        conversation_id: str | None = None,
        settings: AttachmentSettings | None = None,
    ) -> RagResult:
        """Answer from files the asker attached, not from the corpus.

        ``attachments`` is ``(filename, text, truncated)``, oldest first.

        No confidence gate here by design: there is no cosine for an unembedded
        file, and attaching it answers that question. The strict prompt still
        refuses when the file does not answer. Scrubbing and fencing are
        unchanged: an upload is as untrusted as a synced document. Short files go
        whole, long ones are paged; both end in `_generate`.
        """
        files = [
            AttachedFile(name, text, cut)
            for name, text, cut in attachments
            if text.strip()
        ]
        if not files:
            return RagResult(
                answer=self._settings.fallback_response,
                answered=False,
                source=SOURCE_NONE,
                sources=[],
                top_score=None,
            )

        contexts = self.attachment_contexts(
            question,
            attachments,
            settings=settings,
            org_id=org_id,
            conversation_id=conversation_id,
        )

        result = self._generate(
            question,
            [],
            None,
            retrieval_reused=False,
            org_id=org_id,
            conversation_id=conversation_id,
            contexts=contexts,
            profile=ATTACHMENT_PROMPT_PROFILE,
        )
        # No agent label: the attachment is not that agent's corpus.
        return replace(
            result,
            source=SOURCE_ATTACHMENT if result.answered else SOURCE_NONE,
        )

    def _page_attachments(
        self,
        question: str,
        files: list[AttachedFile],
        settings: AttachmentSettings,
        *,
        org_id: str | None,
        conversation_id: str | None,
    ) -> list[str]:
        """ONE tool round: show previews, let the model pick what to read.

        Never raises, never empty: any failure falls back to each file's head.
        One round, not a loop: reads are slices of in-memory text.
        """
        # The floor: each file's head, sharing the inline budget.
        share = max(1, settings.inline_char_budget // len(files))
        fallback = [
            f"Attached file: {f.filename} (first {share} characters)\n\n"
            + f.text[:share]
            for f in files
        ]

        progress.report("Picking the relevant parts of the long file" if len(files) == 1
                        else "Picking the relevant parts of the long files")
        prompt = build_attachment_paging_prompt(
            question=question,
            preview_block=build_preview_block(files, settings.preview_chars),
        )
        try:
            result = self._llm.generate_with_tools(
                [{"role": "user", "content": prompt}],
                tools=[READ_FILE_TOOL],
                tool_choice="auto",
            )
        except NotImplementedError:
            # A model without function calling: routine, not a defect.
            logger.info("Attachments: provider has no tool support; using file heads")
            return fallback
        except LLMProviderError:
            logger.warning("Attachments: paging call failed; using file heads", exc_info=True)
            return fallback

        calls = [(c.name, _safe_json_args(c.arguments)) for c in result.tool_calls]
        contexts = run_reads(
            files,
            calls,
            max_reads=settings.max_reads,
            max_read_chars=settings.max_read_chars,
        )
        if not contexts:
            return fallback
        logger.info(
            "Attachments: paged %d section(s) from %d file(s)", len(contexts), len(files)
        )
        return contexts

    def _generate(self, question: str, hits: list[RetrievedChunk], top_score, **kw) -> RagResult:
        """Grounded generation, plus graph facts when a plan is active.

        Facts ride only on retrieved answers with hits, after the chunks, and
        never touch the gate.
        """
        facts, shaped = (None, False)
        if kw.get("contexts") is None and hits:
            facts, shaped = _graph_facts_block(kw.get("org_id"), self._source_provider)
            tools = _connected_tools(kw.get("org_id"))
            if tools and kw.get("profile") is None:
                # A connected answer reads several tools, so drop the "only from <tool>" framing.
                kw["profile"] = connected_prompt_profile(
                    tools, self._prompt_profile.source_label
                )
        result = self._generate_core(question, hits, top_score, graph_facts=facts, **kw)
        return replace(result, graph_shaped=True) if shaped else result

    def _generate_core(
        self,
        question: str,
        hits: list[RetrievedChunk],
        top_score: float | None,
        *,
        graph_facts: str | None = None,
        retrieval_reused: bool,
        org_id: str | None = None,
        conversation_id: str | None = None,
        budget: RequestBudget | None = None,
        user_question: str | None = None,
        contexts: list[str] | None = None,
        extra_contexts: list[str] | None = None,
        extra_blocks: list | None = None,
        profile: PromptProfile | None = None,
        superseded: frozenset[str] = frozenset(),
    ) -> RagResult:
        # `contexts` comes only from `answer_from_attachments`. Everything below
        # (prompt, MODE tag, refusal, tone retry, audit) is reused: no weaker path.
        if contexts is None:
            # Only `_whole_scope` returns more hits than a ranked read, so this is how
            # generation knows the corpus is read whole. A whole read gets no second
            # budget: `assemble_context_texts` keeps a prefix and would keep the OLDEST
            # threads. `_whole_scope` already enforced the budget.
            # ponytail: a whole read of top_k..wide_max_hits chunks looks ranked and
            # gets the wide budget, which already fits that many; thread an explicit
            # flag from `_whole_scope` if the two limits ever diverge.
            whole_read = len(hits) > self._read_settings().ranked_max_hits
            screened = _screen_hits(hits, org_id, self._guard_settings)
            if hits and not screened and not extra_contexts:
                # Everything retrieved was flagged: refuse without a model call.
                return RagResult(
                    answer=self._settings.fallback_response,
                    answered=False,
                    source=SOURCE_NONE,
                    sources=[],
                    top_score=top_score,
                    retrieval_reused=retrieval_reused,
                )
            hits = screened
            # A document read LIVE is in `extra_contexts`; its synced chunks would be a
            # second, older copy. They stay in `hits` for citations and reuse.
            prompt_hits = [h for h in hits if h.document_id not in superseded]
            contexts = assemble_context_texts(
                # Title and provenance (provider, editor, date), so "who wrote this?" and
                # "what happened recently?" can be answered.
                [describe_hit(h) for h in prompt_hits],
                0 if whole_read else self._read_settings().context_chars_for(len(hits)),
            )
            # The budget keeps a PREFIX, so block i is prompt_hits[i].
            blocks: list = list(prompt_hits[: len(contexts)])
            progress.found(blocks, deep_docs=self._settings.deep_top_docs if deep_read_on() else 0)
        else:
            blocks = [None] * len(contexts)
        # Attached files go FIRST: the document under discussion leads and the
        # corpus supports it. Each block carries its own provenance line.
        progress.report("Writing the answer")
        if extra_contexts:
            contexts = list(extra_contexts) + contexts
            # A live read is the indexed document, so it is citable; an attachment is
            # not. A mismatched list would cite the wrong document.
            lead = list(extra_blocks) if extra_blocks is not None else []
            if len(lead) != len(extra_contexts):
                lead = [None] * len(extra_contexts)
            blocks = lead + blocks
        if graph_facts:
            contexts = list(contexts) + [graph_facts]
            blocks = blocks + [None]
        tone_source = user_question or question
        # Tone runs alongside generation; only the empathy opener uses it.
        tone_future = _AUX_POOL.submit(
            contextvars.copy_context().run,
            self._classify_question_tone,
            tone_source,
            org_id=org_id,
            conversation_id=conversation_id,
            budget=budget,
        )

        asker = current_asker_facts()
        from ..insights.table_answers import NARROWER, current_answers

        prompt = build_grounded_prompt(
            question=question,
            contexts=contexts,
            fallback_response=self._settings.fallback_response,
            asker_facts=asker,
            # The attachment path drops the agent's escalation hint: wrong contact for
            # an uploaded file.
            profile=profile or self._prompt_profile,
            focus_rule=self._settings.focus_rule,
            partial_rule=self._settings.partial_rule,
            conflict_rule=self._settings.conflict_rule,
            assemble_rule=self._settings.assemble_rule and extra_effort_on(),
            # A worked figure and the part of it the question may have meant.
            readings_rule=any(NARROWER in a.text for a in current_answers()),
        )
        answer_cap = self._read_settings().max_answer_tokens  # extra effort may answer longer
        raw = self._generate_text(
            STAGE_GENERATE,
            prompt,
            org_id=org_id,
            conversation_id=conversation_id,
            max_tokens=answer_cap,
        ).strip()
        mode, text = _parse_tagged_mode(raw)
        if current_live_request() is not None:
            # A live handle the model echoed ("per [L1]") is machinery, never shown.
            text = strip_handles(text)

        tone_retry_used = False
        min_stage = self._budget_settings.min_stage_seconds
        meta_bad = mode in ("A", "B") and _violates_mode_b_tone(text)
        if (
            meta_bad
            and budget is not None
            and budget.can_spend(min_stage)
        ):
            retry_raw = self._generate_text(
                STAGE_TONE_RETRY,
                prompt + _tone_retry_addendum(mode or "A"),
                org_id=org_id,
                conversation_id=conversation_id,
                max_tokens=answer_cap,
            ).strip()
            tone_retry_used = True
            retry_mode, retry_text = _parse_tagged_mode(retry_raw)
            mode, text = retry_mode, retry_text

        try:
            question_tone = tone_future.result(timeout=_TONE_GRACE_SECONDS)
        except FutureTimeout:
            question_tone = None
        opener: str | None = None
        if question_tone == "supportive":
            opener = self._empathy_opener(
                tone_source,
                org_id=org_id,
                conversation_id=conversation_id,
                budget=budget,
            )

        answered = not self._is_refusal(text, self._settings.fallback_response)
        answer = text if answered else self._settings.fallback_response
        if answered and leaks_canary(answer):
            # The model repeated its own instructions (an injection's goal): ship none of it.
            logger.warning("security.canary_leak stage=generate org=%s", org_id)
            answered, answer = False, self._settings.fallback_response
        moderation = None
        cited: list[dict] = []
        if answered:
            # Before the audit, so the audit judges the text that will ship.
            answer = enforce_link_provenance(answer, contexts, self._link_allowlist)
            answer, cited = link_citations(answer, blocks)
            # In parallel with the audit below; a no-op when off.
            moderation = _AUX_POOL.submit(
                answer_is_unsafe, strip_citations(answer), org_id=org_id, stage="generate"
            )

        audit_used = False
        audit_downgraded = False
        audit_reason: str | None = None
        if (
            answered
            and mode in ("A", "B")
            and self._audit_settings.enabled
            and budget is not None
            and budget.can_spend(self._budget_settings.min_stage_seconds)
        ):
            verdict = self._audit_answer(
                question, contexts, strip_citations(answer),
                org_id=org_id, conversation_id=conversation_id,
            )
            if verdict is not None and verdict.grounded is not None:
                audit_used = True
                if not verdict.grounded:
                    audit_downgraded = True
                    audit_reason = verdict.reason
                    answered = False
                    answer = self._settings.fallback_response

        if answered and moderation is not None:
            try:
                unsafe = moderation.result(timeout=self._guard_settings.timeout + 1)
            except Exception:  # noqa: BLE001 - fail open
                unsafe = False
            if unsafe:
                answered, answer = False, self._settings.fallback_response

        if answered and opener:
            # Composed after the link rule, from the question alone: its own pass.
            opener = enforce_link_provenance(opener, [], ())
            answer = compose_supportive_answer(opener, answer)

        return RagResult(
            answer=answer,
            answered=answered,
            source=self._prompt_profile.source_label if answered else SOURCE_NONE,
            sources=hits,
            top_score=top_score,
            retrieval_reused=retrieval_reused,
            response_mode=mode,
            tone_retry_used=tone_retry_used,
            question_tone=question_tone,
            audit_used=audit_used,
            audit_downgraded=audit_downgraded,
            audit_reason=audit_reason,
            personalized=bool(asker),
            cited=cited if answered else [],
        )

    def _audit_answer(
        self,
        question: str,
        contexts: list[str],
        answer: str,
        *,
        org_id: str | None,
        conversation_id: str | None,
    ):
        """One bounded groundedness check. ``None`` on any failure (skip audit)."""
        if self._audit_settings.backend == "lettuce":
            return lettuce_verdict(self._audit_settings, question, contexts, answer)
        try:
            raw = self._generate_text(
                STAGE_AUDIT,
                build_audit_prompt(question, contexts, answer),
                org_id=org_id,
                conversation_id=conversation_id,
                max_tokens=120,
            )
        except Exception:
            return None
        return parse_audit_verdict(raw)

    def _recover_once(
        self,
        question: str,
        org_id: str,
        prior_hits: list[RetrievedChunk],
        *,
        reason: str,
        budget: RequestBudget,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
        viewer: Viewer | None = None,
    ) -> _RecoveryAttempt:
        """One bounded recovery: expand → re-retrieve → fuse. Any failure returns
        the prior hits."""
        del reason
        progress.report("Searching again with different wording")
        queries = self._expand_recovery_queries(
            question, prior_hits, org_id=org_id, conversation_id=conversation_id
        )
        if not queries:
            return _RecoveryAttempt(
                hits=list(prior_hits),
                gate_score=prior_hits[0].score if prior_hits else None,
                queries=[],
                gate_document_id=gate_document(prior_hits[:1]),
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
                gate_document_id=gate_document(prior_hits[:1]),
            )

        for q_text, q_vec in zip(queries, vectors):
            try:
                hits, _, _ = self._retrieve_once(
                    org_id,
                    q_text,
                    q_vec,
                    workspace_id=workspace_id,
                    date_range=date_range,
                    tags=tags,
                    viewer=viewer,
                )
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

        gate_score = max((c.score for c in fused), default=None)
        gate_doc = gate_document(fused)
        fused = fused[: self._settings.top_k]
        return _RecoveryAttempt(
            hits=fused, gate_score=gate_score, queries=queries, gate_document_id=gate_doc
        )

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
                STAGE_RECOVERY_EXPAND,
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
        workspace_id: str | None = None,
        viewer: Viewer | None = None,
        query_vec: list[float] | None = None,
        user_question: str | None = None,
        live_withheld: str | None = None,
    ) -> RagResult:
        """Evidence insufficient: try web search (if enabled), else fallback.

        Every refusal passes here, so the withheld-documents check lives here.
        ``live_withheld`` names the connector whose live read said the item is gone.
        """
        min_stage = self._budget_settings.min_stage_seconds
        web_decided = False
        if (
            not live_withheld
            and hits
            and current_live_request() is not None
            and budget.can_spend(min_stage * 2)
        ):
            # Live mode B: may ONE related item be read live? Only on the way to a
            # refusal, in the same call as the web decision.
            outcome, web_decided = self._try_live_decision(
                question, hits, top_score, budget=budget, org_id=org_id,
                conversation_id=conversation_id, user_question=user_question,
            )
            if outcome is not None:
                return outcome
        # A live-withheld item is internal: the web must not replace that notice.
        if (
            not live_withheld
            and not web_decided
            and self._web_search is not None
            and self._web_search_settings.enabled
            and budget.can_spend(min_stage * 2)
        ):
            web = self._try_web_search(
                question,
                top_score,
                org_id=org_id,
                conversation_id=conversation_id,
                user_question=user_question,
            )
            if web is not None:
                return web

        # After web search, on refusals only: replaces "I don't know" with the real
        # reason, never an answer.
        answer = self._settings.fallback_response
        access_restricted = False
        if live_withheld:
            # The provider just told us; no second query needed to confirm it.
            answer = live_withheld_notice(live_withheld)
        elif org_id and query_vec is not None and viewer is not None:
            notice = self._withheld_notice(
                org_id, query_vec, workspace_id=workspace_id, viewer=viewer
            )
            if notice is not None:
                answer = notice
                access_restricted = True

        return RagResult(
            answer=answer,
            answered=False,
            source=SOURCE_NONE,
            sources=hits,
            top_score=top_score,
            budget_exhausted=not budget.can_spend(min_stage),
            access_restricted=access_restricted,
            live_withheld=bool(live_withheld),
        )

    def _withheld_notice(
        self,
        org_id: str,
        query_vec: list[float],
        *,
        workspace_id: str | None,
        viewer: Viewer,
    ) -> str | None:
        """"Not shared with you" instead of "I don't know", when TRUE.

        Uses the answer path's ``similarity_threshold``, so it claims only that a
        document that would have passed the gate was withheld. Never raises.
        """
        # A Slack channel reply reads that channel only, so only its threads may
        # confirm a withheld match.
        channel_tags = [channel_tag(c) for c in viewer.channels if c] or None
        try:
            match = self._store.restricted_match(
                org_id,
                query_vec,
                workspace_id=workspace_id,
                source_provider=self._source_provider,
                viewer=viewer,
                min_score=self._settings.similarity_threshold,
                tags=channel_tags,
            )
        except Exception:  # noqa: BLE001
            logger.exception("restricted_match failed; falling back to the fixed refusal")
            return None
        if match is None:
            return None
        return restricted_notice(
            match.source_provider,
            org_id=org_id,
            workspace_id=workspace_id,
            in_channel=bool(channel_tags),
        )

    # -- retrieval reuse (deterministic, no LLM) ---------------------------

    def _try_reuse(
        self,
        question: str,
        org_id: str,
        query_vec: list[float],
        conversation_id: str | None,
    ) -> RetrievalResult | None:
        """Reuse the previous turn's chunks when the best cosine clears
        ``reuse.threshold`` (re-scored against THIS question), else ``None``.
        No LLM call."""
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

        # Re-embed the previous chunks rather than store vectors. Truncated to a
        # chunk-sized slice: a Deep block overran the embedder's token limit. Any
        # failure retrieves fresh.
        try:
            prev_vecs = self._embedder.embed([r.content[:DEFAULT_MAX_CHUNK_CHARS] for r in prev])
        except Exception:  # noqa: BLE001 - a shortcut must never cost an answer
            logger.warning("reuse check skipped: could not embed the previous turn's chunks", exc_info=True)
            return None
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
                document_title=getattr(prev[i], "document_title", None),
            )
            for i in order
        ][: self._settings.top_k]
        # gate_score == best cosine among reused chunks, mirroring fresh retrieval.
        return RetrievalResult(hits=hits, gate_score=best, gate_document_id=gate_document(hits[:1]))

    def _try_live_decision(
        self,
        question: str,
        hits: list[RetrievedChunk],
        top_score: float | None,
        *,
        budget: RequestBudget,
        org_id: str | None,
        conversation_id: str | None,
        user_question: str | None,
    ) -> tuple[RagResult | None, bool]:
        """Mode B: ``(result, web_decided)``. Never raises.

        The model may name ONE related item already cleared for this asker; the
        handle resolves from this request's map, is read live, and the answer
        goes through the normal grounded path. ``web_decided`` means the same
        call settled the web question.
        """
        from ..config.settings import LiveToolsSettings
        from ..livetools.gateway import candidates as live_candidates

        request = current_live_request()
        try:
            settings = LiveToolsSettings.from_env()
            if request is None or not request.user_id or not settings.allows(request.org_id):
                return None, False
            minted = mint_handles(live_candidates(hits, request, settings))
        except Exception:  # noqa: BLE001 - mode B may only ever add
            logger.warning("live decision skipped", exc_info=True)
            return None, False
        if not minted:
            return None, False

        from ..rag.context_assemble import _PROVIDER_LABEL

        catalog = "\n".join(
            f"[{h}] {title or 'Untitled'} ({_PROVIDER_LABEL.get(provider, provider)})"
            for h, (_doc, provider, title) in minted.items()
        )
        web = self._web_search is not None and self._web_search_settings.enabled
        tools = [REFRESH_ITEM_TOOL] + ([WEB_SEARCH_TOOL] if web else [])
        prompt = build_live_decision_prompt(
            question, catalog, self._settings.fallback_response, web=web
        )
        try:
            # Machinery, like the web decision: pinned to the default model.
            with default_model_only():
                decision = self._llm.generate_with_tools(
                    [{"role": "user", "content": prompt}], tools=tools, tool_choice="auto"
                )
            log_llm_call(STAGE_WEB_DECISION, self._llm, org_id=org_id,
                         conversation_id=conversation_id)
        except LLMProviderError:
            return None, False

        calls = list(decision.tool_calls or [])
        if not calls:
            return None, web  # declined both: the web question was asked too
        call = calls[0]
        if call.name == "web_search":
            if not web:
                return None, False
            return self._try_web_search(question, top_score, org_id=org_id,
                                        conversation_id=conversation_id,
                                        user_question=user_question,
                                        decision=decision), True
        if call.name != "refresh_item":
            return None, web
        document_id = resolve_handle(_safe_json_args(call.arguments).get("handle"), minted)
        if document_id is None:
            logger.info("livetools.unknown_handle org=%s", org_id)
            return None, web
        chosen = [h for h in hits if h.document_id == document_id]
        live = live_refresh(chosen, request, guard_settings=self._guard_settings, mode="model")
        if document_id in live.withheld:
            return self._gate_failed(
                question, hits=[h for h in hits if h.document_id != document_id],
                top_score=top_score, budget=budget, org_id=org_id,
                conversation_id=conversation_id, user_question=user_question,
                live_withheld=live.withheld[document_id],
            ), True
        if not live.blocks:
            return None, web
        result = self._generate(
            question, chosen, top_score, retrieval_reused=False, org_id=org_id,
            conversation_id=conversation_id, budget=budget, user_question=user_question,
            extra_contexts=live.blocks, extra_blocks=live.cite_blocks,
            superseded=live.refreshed,
        )
        if not result.answered:
            return None, web
        return replace(result, live_sources=live.sources), True

    def _screen_web_results(self, results: list) -> list:
        """Drop (enforce) or log (shadow) snippets that read like an injection.

        One batched call. A guard failure keeps every result: link provenance
        and the web fence still apply.
        """
        guard = self._injection_guard
        if guard is None or not results:
            return results
        try:
            scores = guard.score([f"{r.title}\n{r.snippet}" for r in results])
        except Exception:  # noqa: BLE001 - fail open
            return results
        kept = []
        for r, score in zip(results, scores):
            if score is not None and score >= self._guard_settings.threshold:
                logger.warning(
                    "guard.flagged_web mode=%s score=%.3f host=%s",
                    self._guard_settings.mode, score, urlparse(r.url).hostname,
                )
                if self._guard_settings.mode == "enforce":
                    continue
            kept.append(r)
        return kept

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
                document_title=c.document_title,
            )
            for c in result.sources
        ]
        self._memory.set_last_retrieval(conversation_id, org_id, records)

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """Cosine similarity, on the same scale as pgvector's ``1 - distance``.
        Full form so a non-normalizing backend still matches the gate."""
        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if denom == 0.0:
            return 0.0
        return float(np.dot(va, vb) / denom)

    # -- web-search tool use (single step) ---------------------------------

    def _try_web_search(
        self,
        question: str,
        top_score: float | None,
        *,
        org_id: str | None = None,
        conversation_id: str | None = None,
        user_question: str | None = None,
        decision=None,
    ) -> RagResult | None:
        """One decision call + at most one search + one answer call.

        Any failure returns ``None`` (fixed fallback). ``decision``: one already
        made in the live-read call.
        """
        if decision is not None:
            return self._run_web_search(decision, question, top_score, org_id=org_id,
                                        conversation_id=conversation_id,
                                        user_question=user_question)
        decision_prompt = build_web_decision_prompt(
            question, self._settings.fallback_response
        )
        messages = [{"role": "user", "content": decision_prompt}]
        try:
            # Machinery: pinned to the default model, which also needs dependable tool
            # calling.
            with default_model_only():
                decision = self._llm.generate_with_tools(
                    messages, tools=[WEB_SEARCH_TOOL], tool_choice="auto"
                )
            log_llm_call(
                STAGE_WEB_DECISION,
                self._llm,
                org_id=org_id,
                conversation_id=conversation_id,
            )
        except LLMProviderError:
            return None

        if not decision.tool_calls:
            return None  # model judged the question internal -> fixed fallback
        return self._run_web_search(decision, question, top_score, org_id=org_id,
                                    conversation_id=conversation_id,
                                    user_question=user_question)

    def _run_web_search(
        self, decision, question: str, top_score: float | None, *,
        org_id: str | None, conversation_id: str | None, user_question: str | None,
    ) -> RagResult | None:
        """The search + answer half of the web path, for a decision in hand."""
        query = user_worded_query(
            self._extract_query(decision.tool_calls[0].arguments, question),
            self._user_texts(user_question or question, conversation_id),
        )
        if query is None:
            logger.info("security.web_query_dropped: mostly words the user never typed")
            return None

        progress.report("Searching the web")
        try:
            results = self._web_search.search(
                query,
                max_results=self._web_search_settings.max_results,
                timeout=self._web_search_settings.timeout,
            )
        except WebSearchError:
            return None  # graceful degradation on failure/timeout
        results = self._screen_web_results(results or [])
        if not results:
            return None

        results_block = "\n\n".join(
            f"[{i + 1}] {r.title}\n{r.snippet}\n{r.url}" for i, r in enumerate(results)
        )
        try:
            raw = self._generate_text(
                STAGE_WEB_ANSWER,
                build_web_answer_prompt(question, results_block),
                org_id=org_id,
                conversation_id=conversation_id,
            ).strip()
        except LLMProviderError:
            return None

        # We append the Sources list; only the model's prose is checked.
        if leaks_canary(raw):
            logger.warning("security.canary_leak stage=web org=%s", org_id)
            return None
        raw = enforce_link_provenance(raw, [results_block], self._link_allowlist)
        if answer_is_unsafe(raw, org_id=org_id, stage="web"):
            return None
        return RagResult(
            answer=self._format_web_answer(raw, results),
            answered=True,
            source=SOURCE_WEB,
            sources=[],
            top_score=top_score,
        )

    def _user_texts(self, latest: str, conversation_id: str | None) -> list[str]:
        """The asker's earlier QUESTIONS (never answers or documents), so a
        follow-up can still search for a name given turns ago."""
        texts = [latest]
        if conversation_id and self._memory is not None:
            try:
                texts += [t.question for t in self._memory.get_turns(conversation_id)]
            except Exception:  # noqa: BLE001 - fewer words, never a failed answer
                logger.warning("web query: could not read earlier questions", exc_info=True)
        return texts

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
        """Corpus-vocab spelling fix for the retrieval key.

        The corpus is a thunk: it is the org's entire chunk text, read only on a
        cache miss of the normalizer's per-org dictionary.
        """
        if not self._query_norm.enabled:
            return question

        def corpus() -> list[str]:
            try:
                return self._store.list_chunk_texts(org_id)
            except NotImplementedError:
                return []

        try:
            return self._query_norm.normalize(question, org_id, corpus)
        except Exception:  # noqa: BLE001
            return question

    # -- conversation memory helpers ---------------------------------------

    def _rewrite_question(
        self,
        question: str,
        context: ConversationContext,
        *,
        org_id: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        recent = [(t.question, t.answer) for t in context.recent_turns]
        prompt = build_rewrite_prompt(
            question, context.summary, recent, asker_context=current_asker_context()
        )
        try:
            rewritten = self._generate_text(
                STAGE_REWRITE,
                prompt,
                org_id=org_id,
                conversation_id=conversation_id,
            ).strip()
        except LLMProviderError:
            return question

        first_line = rewritten.splitlines()[0].strip() if rewritten else ""
        if not first_line or len(first_line) > 300 or not first_line.endswith("?"):
            return question
        return first_line

    def _update_running_summary(self, conversation_id: str) -> None:
        """Fold the turn(s) that left the verbatim window into the running summary.

        Runs in the background after every turn. Input is the summary plus the
        turns past the fold marker, so cost stays about constant.
        """
        window = self._memory_settings.recent_turns
        turns = self._memory.get_turns(conversation_id)
        if len(turns) <= window:
            return

        # Turns are kept, so what is already folded comes from the marker; without
        # it every turn would re-summarise the whole conversation.
        folded_through = self._memory.get_folded_through(conversation_id)
        newest_to_fold = turns[-window].turn_index - 1 if window > 0 else turns[-1].turn_index
        falling_out = [
            t
            for t in turns
            if t.turn_index <= newest_to_fold
            and (folded_through is None or t.turn_index > folded_through)
        ]
        if not falling_out:
            # Everything outside the window is already summarised: no LLM call.
            return

        existing = self._memory.get_summary(conversation_id)
        prompt = build_summary_prompt(
            existing, [(t.question, t.answer) for t in falling_out]
        )
        try:
            summary = self._generate_text(
                STAGE_SUMMARY_FOLD,
                prompt,
                conversation_id=conversation_id,
            ).strip()
        except LLMProviderError:
            return
        # Model-written from possibly injected text, and stored: clean it.
        summary = strip_links(scrub_untrusted_text(summary))
        if summary:
            self._memory.set_summary_folded_through(
                conversation_id, summary, falling_out[-1].turn_index
            )

    @staticmethod
    def _is_refusal(text: str, fallback_response: str) -> bool:
        """True if the reply is essentially the fixed refusal (robust to
        punctuation, case and wrapping)."""
        core = fallback_response.rstrip(".").strip().lower()
        return core in text.strip().lower()
