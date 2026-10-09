"""Hybrid retrieval + reranking (Phase 6), sitting under the Phase 3 gate.

Plain top-k vector search ranks each chunk independently and can leave a genuinely
relevant chunk just outside the cutoff. This retriever addresses that from two
angles at query time:

1. **Hybrid search** — run vector (semantic) *and* keyword (Okapi BM25) search,
   then fuse the two ranked lists with **Reciprocal Rank Fusion (RRF)**. RRF is
   rank-based, so it needs no score normalization between cosine and BM25
   (which live on totally different scales) — the settled default for hybrid RAG.
2. **Cross-encoder reranking** — over-retrieve a wider ``candidate_pool`` then
   rerank it with a cross-encoder, selecting the final ``top_k``.

Crucially this only changes *which chunks, in what order* reach the prompt. The
**confidence gate is unchanged**: ``gate_score`` is the best cosine similarity
among candidates (== the vector top-1 the Phase 3 gate always used), so the
pipeline's threshold logic behaves exactly as before.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from ..config.settings import GraphSettings, RagSettings, RetrievalSettings
from ..reranker.base import Reranker
from ..vectorstore.base import DateRange, RetrievedChunk, VectorStore, Viewer

logger = logging.getLogger(__name__)
#: Beside `rag.query_signals`: one line per question the graph list ran for,
#: so step 1.7 can measure how often it links and what it contributes.
_graph_log = logging.getLogger("rag.graph_signals")


# Ceiling on concurrent first-stage searches for ONE question. Each in-flight
# search holds a pooled connection, and DB_POOL_MAX_SIZE defaults to 10 shared
# across the whole process — so this stays well under it, leaving room for
# concurrent requests. Raising it trades tail latency for pool contention: past
# the pool size the extra tasks just queue on a connection instead of a query.
_MAX_RETRIEVAL_WORKERS = 4


@dataclass(frozen=True)
class RetrievalResult:
    """What the retriever hands back to the pipeline."""

    hits: list[RetrievedChunk]  # final top_k, best-first (fused + reranked)
    gate_score: float | None    # best cosine similarity among candidates (gate signal)
    # Chunks the knowledge graph contributed to the first stage (0 when the
    # graph list is off or found nothing) -- logged so step 1.7 can measure it.
    graph_hits: int = 0
    # The document of the candidate that produced ``gate_score``. The final
    # ``hits`` cannot reproduce the gate (it is a max over every first-stage
    # candidate, taken BEFORE reranking cuts to top_k), so a caller that must
    # re-check the gate after withholding a document -- a live read saying
    # the object is gone -- asks "was the gate earned by THIS document?"
    # instead of recomputing over hits that never held the max.
    gate_document_id: str | None = None


class HybridRetriever:
    """Vector + keyword retrieval, RRF fusion, then cross-encoder reranking.

    An orchestrator (composes the store + reranker), so no ``base.py`` — like the
    RAG pipeline itself. ``reranker`` is optional; when absent (or disabled) the
    fused/vector order is used directly.
    """

    def __init__(
        self,
        store: VectorStore,
        reranker: Reranker | None = None,
        settings: RetrievalSettings | None = None,
        rag_settings: RagSettings | None = None,
        source_provider: str | None = None,
        graph_settings: GraphSettings | None = None,
    ) -> None:
        self._store = store
        self._graph_settings = graph_settings or GraphSettings.from_env()
        self._reranker = reranker
        self._settings = settings or RetrievalSettings.from_env()
        self._rag_settings = rag_settings or RagSettings.from_env()
        # Pinned per-retriever rather than per-call: which providers an agent
        # may draw on is a property of the agent (a Slack agent is always a
        # Slack agent), not of one question. Keeping it off the call signature
        # also means it can never be forgotten at one of the retrieve() call
        # sites the way a per-request argument could be.
        self._source_provider = source_provider

    def retrieve(
        self,
        org_id: str,
        query_text: str,
        query_embedding: list[float],
        *,
        extra_queries: list[tuple[str, list[float]]] | None = None,
        rerank_query: str | None = None,
        workspace_id: str | None = None,
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
        viewer: Viewer | None = None,
    ) -> RetrievalResult:
        """Retrieve for ``query_text``; optionally fuse extra (sub-)queries first.

        ``workspace_id`` (Workspace-within-a-Workspace): ``None`` (default)
        retrieves only org-wide chunks, unchanged from every prior call site.
        Non-``None`` retrieves only that sub-workspace's chunks — passed
        straight through to the store, never widened to also include the
        org-wide space (see CLAUDE.md's Workspace-within-a-Workspace plan).

        ``date_range``: an optional hard filter (e.g. "updated after March")
        passed straight through to both the vector and keyword legs — a
        no-op when ``None``, identical to every existing call site.

        ``tags``: an optional hard filter (e.g. department labels), same
        no-op-when-``None`` and pass-through-to-both-legs behaviour as
        ``date_range``.

        ``viewer`` (document-level access): WHO is asking, passed to both legs
        so a document the asker cannot open cannot reach them through either.
        ``None`` reads every document in scope, which is what ingestion,
        evaluation and the CLI want and what every member-facing path must NOT
        pass — see ``tests/test_doc_access.py``.
        """
        top_k = self._rag_settings.top_k
        pool = self._settings.candidate_pool
        rerank_q = rerank_query or query_text

        query_pairs = [(query_text, query_embedding)]
        if extra_queries:
            query_pairs.extend(extra_queries)

        graph_documents, graph_cross = self._graph_documents(
            org_id, workspace_id, query_text, viewer
        )
        extra_tools = self._named_tools(org_id, workspace_id)
        graph_cross = graph_cross or bool(extra_tools)
        graph_counter: list[int] = []
        ranked_lists = self._first_stage_all(
            org_id,
            query_pairs,
            pool,
            workspace_id=workspace_id,
            date_range=date_range,
            tags=tags,
            viewer=viewer,
            graph_documents=graph_documents,
            graph_counter=graph_counter,
            graph_cross=graph_cross,
            extra_tools=extra_tools,
        )
        graph_hits = sum(graph_counter)
        if graph_documents:
            _graph_log.info(json.dumps({"event": "graph_hits", "org_id": org_id, "graph_hits": graph_hits}))

        if len(ranked_lists) == 1:
            candidates = ranked_lists[0]
        else:
            candidates = self._rrf_fuse(ranked_lists, self._settings.rrf_k)

        if not candidates:
            return RetrievalResult(hits=[], gate_score=None, graph_hits=graph_hits)

        gate_score = max((c.score for c in candidates), default=None)
        gate_document_id = gate_document(candidates)

        pool_candidates = candidates[:pool]
        # The selection below sees the WHOLE reranked pool. Cutting it to the
        # top 10 pieces first hid documents ranked lower than one long
        # document's many pieces from the wide read (RCA, 9 Oct: 62% -> 66% of
        # the right documents for many-document questions, simple questions
        # unchanged). The reranker scores every candidate either way; the
        # selection still returns at most ``ranked_max_hits``.
        rag = self._rag_settings
        keep = len(pool_candidates)
        if self._reranker is not None and self._settings.rerank_enabled:
            reranked = self._rerank(rerank_q, pool_candidates, keep)
            final = _drop_weak(reranked, self._settings.rerank_min_ratio)
            if not graph_cross:
                final = _spread(final, top_k, rag.wide_max_hits, rag.wide_doc_ratio, rag.wide_per_doc,
                                rag.wide_include_ratio, pool=reranked)
        else:
            final = pool_candidates[: keep if graph_cross else rag.ranked_max_hits]
        if graph_cross:
            final = _reserve_other_tools(final, top_k, self._source_provider)

        return RetrievalResult(
            hits=final, gate_score=gate_score, graph_hits=graph_hits,
            gate_document_id=gate_document_id,
        )

    def _rerank(self, query: str, candidates: list[RetrievedChunk], keep: int) -> list[RetrievedChunk]:
        """The reranker's order; with ``rerank_with_title`` it reads "title\ntext".

        Only what the reranker sees changes: the returned hits are the original
        chunks (content untouched for the prompt and citations) carrying the
        new ``rerank_score``.
        """
        if not self._settings.rerank_with_title:
            return self._reranker.rerank(query, candidates, keep)
        shown = [
            replace(c, content=f"{c.document_title}\n{c.content}") if c.document_title else c
            for c in candidates
        ]
        by_key = {(c.document_id, c.chunk_index): c for c in candidates}
        return [
            replace(by_key[(r.document_id, r.chunk_index)], rerank_score=r.rerank_score)
            for r in self._reranker.rerank(query, shown, keep)
        ]

    def _graph_documents(
        self, org_id: str, workspace_id: str | None, query_text: str, viewer: Viewer | None
    ) -> tuple[list[str], bool]:
        """Documents the knowledge graph connects to this question (Second Brain 1.6).

        ``(document_ids, cross)``. When the chat edge already built this
        question's ``GraphPlan`` it is REUSED -- no second walk -- and its
        ``cross`` flag says whether the documents may come from other tools
        (a connected answer) or only from this retriever's own tool (every
        normal question, exactly as before). Otherwise: link the question to at
        most three entities, walk ≤2 hops as THIS viewer, return the visible
        evidence documents. Empty when the flag is off (the default), when
        nothing links, or on ANY failure: the graph can only ever add
        candidates, so losing it must cost nothing but them.
        """
        if not self._graph_settings.retrieval_enabled:
            return [], False
        from ..graph.plan import current_plan

        plan = current_plan()
        if plan is not None and plan.matches(org_id, workspace_id):
            return plan.documents_for(self._source_provider), plan.cross is not None
        try:
            from ..graph.linking import link_question
            from ..graph.walk import walk

            seeds = link_question(org_id, workspace_id, query_text, viewer)
            result = (
                walk(org_id, workspace_id, [s.id for s in seeds], viewer, question=query_text)
                if seeds else None
            )
            _graph_log.info(
                json.dumps(
                    {
                        "event": "graph_signal",
                        "org_id": org_id,
                        "seeds": len(seeds),
                        "exact_seeds": sum(1 for s in seeds if s.exact),
                        "documents": len(result.document_ids) if result else 0,
                        "edges": result.edges if result else 0,
                        "truncated": bool(result and result.truncated),
                    }
                )
            )
            return (result.document_ids if result else []), False
        except Exception:  # noqa: BLE001 - see docstring
            logger.warning("retrieval: graph list skipped", exc_info=True)
            return [], False

    def _named_tools(self, org_id: str, workspace_id: str | None) -> list[str]:
        """Tools a connected answer searches besides this retriever's own.

        Only ever the ones the question NAMED (``GraphPlan.search``); empty
        for every normal answer, so their searches do not run at all.
        """
        if not self._graph_settings.retrieval_enabled:
            return []
        try:
            from ..graph.plan import current_plan

            plan = current_plan()
            if plan is None or not plan.matches(org_id, workspace_id):
                return []
            return plan.search_tools(self._source_provider)
        except Exception:  # noqa: BLE001 - a named tool may only ever add
            logger.warning("retrieval: named-tool search skipped", exc_info=True)
            return []

    def _first_stage_all(
        self,
        org_id: str,
        query_pairs: list[tuple[str, list[float]]],
        pool: int,
        *,
        workspace_id: str | None = None,
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
        viewer: Viewer | None = None,
        graph_documents: list[str] | None = None,
        graph_counter: list[int] | None = None,
        graph_cross: bool = False,
        extra_tools: list[str] | None = None,
    ) -> list[list[RetrievedChunk]]:
        """Run every first-stage search concurrently, one ranked list per query.

        These searches are independent database round trips — vector and keyword
        for one query, and every sub-question's pair — but they used to run
        strictly one after another, so a three-part compound question serialized
        six queries whose latency is almost entirely waiting on Postgres. The
        pool is deliberately capped: each task takes a connection, and a large
        decomposition must not be able to drain the shared pool.

        Ordering is preserved by index, not completion, because RRF fusion is
        order-sensitive across lists.

        ``graph_documents`` adds the knowledge graph's list: one vector search,
        primary query only, restricted to the walk's evidence documents and
        filtered by the viewer AGAIN (the walk already checked, but retrieval
        never trusts an upstream filter). Ranked by cosine, so each hit carries
        a real cosine ``score`` and the gate is unchanged.
        """
        tasks: list[tuple[int, str, str, list[float]]] = []
        for i, (q_text, q_vec) in enumerate(query_pairs):
            tasks.append((i, "vector", q_text, q_vec))
            if self._settings.hybrid_enabled:
                tasks.append((i, "keyword", q_text, q_vec))
        if graph_documents and query_pairs:
            q_text, q_vec = query_pairs[0]
            tasks.append((0, "graph", q_text, q_vec))
        # A connected answer's NAMED tools: the same vector (+ keyword) search
        # the routed tool runs, pinned to that tool, primary query only. Run in
        # the same pool as everything else, so they add no wall-clock time.
        for tool in extra_tools or []:
            if not query_pairs:
                break
            q_text, q_vec = query_pairs[0]
            tasks.append((0, f"vector:{tool}", q_text, q_vec))
            if self._settings.hybrid_enabled:
                tasks.append((0, f"keyword:{tool}", q_text, q_vec))

        results: dict[tuple[int, str], list[RetrievedChunk]] = {}

        def run(task) -> tuple[tuple[int, str], list[RetrievedChunk]]:
            i, kind, q_text, q_vec = task
            kind, _, tool = kind.partition(":")
            provider = tool or self._source_provider
            if kind == "graph":
                # A connected answer's graph documents were already limited to
                # the tools that answer may use (GraphPlan.documents_for), so
                # the pin is lifted for THIS leg only; the vector and keyword
                # legs below stay on the routed tool whatever happens. The
                # viewer is applied again either way.
                hits = self._store.query(
                    org_id,
                    q_vec,
                    top_k=pool,
                    workspace_id=workspace_id,
                    source_provider=None if graph_cross else self._source_provider,
                    date_range=date_range,
                    tags=tags,
                    viewer=viewer,
                    document_ids=list(graph_documents or []),
                )
            elif kind == "vector":
                hits = self._store.query(
                    org_id,
                    q_vec,
                    top_k=pool,
                    workspace_id=workspace_id,
                    source_provider=provider,
                    date_range=date_range,
                    tags=tags,
                    viewer=viewer,
                )
            else:
                try:
                    hits = self._store.keyword_search(
                        org_id,
                        q_text,
                        q_vec,
                        top_k=pool,
                        workspace_id=workspace_id,
                        source_provider=provider,
                        date_range=date_range,
                        tags=tags,
                        viewer=viewer,
                    )
                except NotImplementedError:
                    hits = []
            return (i, task[1]), list(hits)

        if len(tasks) == 1:
            key, hits = run(tasks[0])
            results[key] = hits
        else:
            workers = min(len(tasks), _MAX_RETRIEVAL_WORKERS)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for key, hits in ex.map(run, tasks):
                    results[key] = hits

        # Per CALL, not on self: one retriever serves concurrent requests.
        if graph_counter is not None:
            graph_counter.append(len(results.get((0, "graph"), [])))
        ranked: list[list[RetrievedChunk]] = []
        for i in range(len(query_pairs)):
            legs = [results.get((i, "vector"), [])]
            if self._settings.hybrid_enabled:
                legs.append(results.get((i, "keyword"), []))
            if (i, "graph") in results:
                legs.append(results[(i, "graph")])
            if i == 0:
                for tool in extra_tools or []:
                    legs.append(results.get((0, f"vector:{tool}"), []))
                    if self._settings.hybrid_enabled:
                        legs.append(results.get((0, f"keyword:{tool}"), []))
            ranked.append(legs[0] if len(legs) == 1 else self._rrf_fuse(legs, self._settings.rrf_k))
        return ranked

    @staticmethod
    def _rrf_fuse(
        ranked_lists: list[list[RetrievedChunk]], k: int
    ) -> list[RetrievedChunk]:
        """Reciprocal Rank Fusion: score(d) = Σ 1/(k + rank_d) across lists.

        Dedupes chunks by (document_id, chunk_index). Each retained chunk keeps its
        cosine ``score`` (both search paths populate it), so the gate signal stays
        a real cosine similarity; RRF only governs ordering.
        """
        rrf_scores: dict[tuple[str, int], float] = {}
        chunk_by_key: dict[tuple[str, int], RetrievedChunk] = {}

        for hits in ranked_lists:
            for rank, hit in enumerate(hits):
                key = (hit.document_id, hit.chunk_index)
                rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k + rank + 1)
                chunk_by_key.setdefault(key, hit)

        ordered = sorted(rrf_scores, key=lambda key: rrf_scores[key], reverse=True)
        return [chunk_by_key[key] for key in ordered]


def _drop_weak(hits: list[RetrievedChunk], ratio: float) -> list[RetrievedChunk]:
    """Keep hits whose reranker score is at least ``ratio`` x the best one's.

    The best hit always stays, so this can thin the prompt but never empty it,
    and the gate (computed before reranking) is untouched. Off at 0, and a no-op
    when the reranker gave no scores or the best score is not positive (a
    ratio of a non-positive number means nothing).
    """
    if ratio <= 0 or not hits:
        return hits
    best = hits[0].rerank_score
    if best is None or best <= 0:
        return hits
    return [hits[0]] + [
        h for h in hits[1:] if h.rerank_score is not None and h.rerank_score >= ratio * best
    ]


def _spread(
    hits: list[RetrievedChunk], top_k: int, max_hits: int, ratio: float, per_doc: int,
    include_ratio: float = 0.0, pool: list[RetrievedChunk] | None = None,
) -> list[RetrievedChunk]:
    """``top_k`` hits, plus more documents when the evidence is spread out.

    A document is "strong" when its best reranker score is at least ``ratio`` x
    the top document's. With one strong document the answer lives in one
    place and the usual ``top_k`` is returned unchanged. With several, the
    usual ``top_k`` is KEPT and passages from strong documents it does not
    already cover are added, up to ``max_hits`` and ``per_doc`` per document:
    new documents first, then a second passage each. So a wide read is always
    a superset of the narrow one and can only reach more documents, never
    fewer (Benchmark 1: more passages of the same documents did not help).
    Reranker scores only; the gate is untouched.

    Detection and inclusion are separate. ``ratio`` decides whether the
    question is broad; once it is, documents down to ``include_ratio`` x the
    best are added, from ``pool`` (the whole reranked list, before the weak
    cutoff). A broad question's right documents are often pieces that answer
    only PART of it, which the reranker scores low (RCA, 9 Oct: 66% -> 74% of
    the right documents for many-document questions at 0.15). A question with
    one strong document is untouched.
    """
    base = hits[:top_k]
    if max_hits <= top_k or ratio <= 0 or not hits:
        return base
    best = hits[0].rerank_score
    if best is None or best <= 0:
        return base
    doc_best: dict[str, float] = {}
    for h in hits:
        if h.rerank_score is not None:
            doc_best.setdefault(h.document_id, h.rerank_score)
    strong = {d for d, s in doc_best.items() if s >= ratio * best}
    if len(strong) <= 1:
        return base
    pool = pool if pool is not None else hits
    floor = min(include_ratio, ratio) if include_ratio > 0 else ratio
    eligible: set[str] = set()
    for h in pool:
        if h.rerank_score is not None and h.rerank_score >= floor * best:
            eligible.add(h.document_id)
    in_base = {(h.document_id, h.chunk_index) for h in base}
    taken: dict[str, int] = {}
    for h in base:
        taken[h.document_id] = taken.get(h.document_id, 0) + 1
    added: list[int] = []
    rest = [(i, h) for i, h in enumerate(pool) if (h.document_id, h.chunk_index) not in in_base]
    for new_docs_only in (True, False):
        for i, h in rest:
            if len(base) + len(added) >= max_hits:
                break
            n = taken.get(h.document_id, 0)
            if i in added or h.document_id not in eligible or n >= per_doc or (new_docs_only and n):
                continue
            taken[h.document_id] = n + 1
            added.append(i)
    return base + [pool[i] for i in sorted(added)]


def gate_document(chunks) -> str | None:
    """The document holding the best cosine among ``chunks`` (the gate's).

    Every candidate's ``score`` is a real cosine -- the vector leg and the
    keyword leg both select ``1 - (embedding <=> q)``, and ``_rrf_fuse`` keeps
    each chunk's own score -- but a hit with no score is skipped rather than
    trusted, so a future score-less leg cannot quietly decide the gate.
    """
    best = max(
        (c for c in chunks if getattr(c, "score", None) is not None),
        key=lambda c: c.score,
        default=None,
    )
    return best.document_id if best is not None else None


#: Slots a connected answer reserves for tools other than the routed one.
_CROSS_RESERVED_SLOTS = 2


def _reserve_other_tools(
    ordered: list[RetrievedChunk], top_k: int, routed: str | None
) -> list[RetrievedChunk]:
    """Top ``top_k``, but with the best chunk of each OTHER tool guaranteed a seat.

    A connected answer exists because the question spans tools ("has the
    author discussed it in Slack?"). With five slots and a corpus that is
    mostly the routed tool's, plain relevance order routinely fills all five
    from it, and the answer then says Slack has nothing when the graph PROVED
    a thread exists. At most ``_CROSS_RESERVED_SLOTS`` swaps, each replacing
    the routed tool's weakest kept chunk, so the routed tool keeps the rest.
    """
    final = list(ordered[:top_k])
    present = {c.source_provider for c in final}
    swaps = 0
    # One seat per other tool, up to all but two of the slots: a question
    # naming four tools must hear from each, and the routed tool keeps two.
    limit = max(_CROSS_RESERVED_SLOTS, min(len({c.source_provider for c in ordered}) - 1,
                                           top_k - 2))
    for chunk in ordered[top_k:]:
        if swaps >= limit:
            break
        tool = chunk.source_provider
        if not tool or tool == routed or tool in present:
            continue
        victims = [i for i, c in enumerate(final) if c.source_provider == routed]
        if len(victims) <= 1:
            break
        final[victims[-1]] = chunk
        present.add(tool)
        swaps += 1
    return final
