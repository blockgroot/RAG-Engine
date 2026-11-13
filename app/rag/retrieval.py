"""Hybrid retrieval + reranking, under the 0.35 gate.

Vector and BM25 search fused by Reciprocal Rank Fusion (rank-based, so no
score normalization), then a cross-encoder reranks a wider
``candidate_pool`` down to ``top_k``. Only which chunks reach the prompt
changes: ``gate_score`` is still the best cosine among candidates.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from ..config.settings import GraphSettings, RagSettings, RetrievalSettings
from ..reranker.base import Reranker
from ..vectorstore.base import DateRange, RetrievedChunk, VectorStore, Viewer
from .deep import extra_effort_on

logger = logging.getLogger(__name__)
#: One line per question the graph list ran for, to measure what it adds.
_graph_log = logging.getLogger("rag.graph_signals")


# Concurrent first-stage searches for ONE question. Each holds a pooled
# connection (DB_POOL_MAX_SIZE is shared process-wide), so stay well under it.
_MAX_RETRIEVAL_WORKERS = 4

#: A table row's passage index: past any real chunk, so it is never taken
#: for a neighbour.
_ROW_INDEX = 1_000_000
#: A figure worked out over a table for this question (past every row index).
_WORKED_INDEX = 2_000_000


@dataclass(frozen=True)
class RetrievalResult:
    """What the retriever hands back to the pipeline."""

    hits: list[RetrievedChunk]  # final top_k, best-first (fused + reranked)
    gate_score: float | None    # best cosine similarity among candidates (gate signal)
    # Chunks the graph contributed to the first stage (0 when off or empty).
    graph_hits: int = 0
    # The document that produced ``gate_score``. ``hits`` cannot reproduce the
    # gate (a max taken before reranking), so a caller withholding a document
    # asks whether the gate was earned by THAT document.
    gate_document_id: str | None = None


class HybridRetriever:
    """Vector + keyword retrieval, RRF fusion, then cross-encoder reranking.
    An orchestrator, so no ``base.py``. Without a reranker the fused order is
    used."""

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
        # Pinned per retriever, not per call: an agent's providers are a property of
        # the agent, and cannot be forgotten at a call site.
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

        ``workspace_id``, ``date_range``, ``tags`` and ``viewer`` pass straight to
        both legs. ``workspace_id=None`` reads org-wide only, never widened.
        ``viewer=None`` reads everything in scope: ingest, eval and CLI only,
        never a member-facing path.
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
        from ..insights.table_answers import current_answers

        # Read here, on the request's thread: the pool does not inherit context.
        worked = current_answers()
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
            worked=worked,
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
        # A row named by a cell comes from ONE leg, which fusion under-rewards, so
        # it always enters the rerank pool; the reranker judges it.
        pooled = {(c.document_id, c.chunk_index) for c in pool_candidates}
        pool_candidates += [c for c in candidates
                            if _ROW_INDEX <= c.chunk_index < _WORKED_INDEX
                            and (c.document_id, c.chunk_index) not in pooled]
        # The selection sees the WHOLE reranked pool (cutting it first hid
        # documents behind one long document's pieces); it still returns at most
        # ``ranked_max_hits``. Deep's extra effort may read more when evidence is spread.
        rag = self._rag_settings.deep(extra=True) if extra_effort_on() else self._rag_settings
        keep = len(pool_candidates)
        if self._reranker is not None and self._settings.rerank_enabled:
            reranked = self._rerank(rerank_q, pool_candidates, keep)
            final = _drop_weak(reranked, self._settings.rerank_min_ratio)
            if not graph_cross:
                final = _spread(final, top_k, rag.wide_max_hits, rag.wide_doc_ratio, rag.wide_per_doc,
                                rag.wide_include_ratio, pool=reranked, always=rag.wide_always)
        else:
            final = pool_candidates[: keep if graph_cross else rag.ranked_max_hits]
        if graph_cross:
            final = _reserve_other_tools(final, top_k, self._source_provider)
        # A figure worked out for this question is never ranked out.
        kept = {(c.document_id, c.chunk_index) for c in final}
        final = final + [c for c in candidates if c.chunk_index >= _WORKED_INDEX
                         and (c.document_id, c.chunk_index) not in kept]

        return RetrievalResult(
            hits=final, gate_score=gate_score, graph_hits=graph_hits,
            gate_document_id=gate_document_id,
        )

    def _rerank(self, query: str, candidates: list[RetrievedChunk], keep: int) -> list[RetrievedChunk]:
        """The reranker's order; with ``rerank_with_title`` it reads "title\ntext".
        Returned hits are the original chunks with ``rerank_score`` set."""
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
        """Documents the knowledge graph connects to this question: ``(ids, cross)``.

        Reuses the chat edge's ``GraphPlan`` when there is one (``cross`` = other
        tools allowed). Otherwise links up to three entities and walks ≤2 hops as
        THIS viewer. Empty when off, unlinked, or on ANY failure: the graph only
        adds candidates.
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
        """Tools a connected answer searches besides this one: the ones the
        question NAMED, or a refusal retry's (``GraphPlan.search``). Empty for a
        normal answer."""
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
        worked: tuple = (),
    ) -> list[list[RetrievedChunk]]:
        """Run every first-stage search concurrently, one ranked list per query.

        Capped so a large decomposition cannot drain the shared pool. Order is by
        index, not completion: RRF is order-sensitive. ``graph_documents`` adds one
        vector search over the walk's documents, filtered by the viewer AGAIN, with
        real cosine scores.
        """
        tasks: list[tuple[int, str, str, list[float]]] = []
        for i, (q_text, q_vec) in enumerate(query_pairs):
            tasks.append((i, "vector", q_text, q_vec))
            if self._settings.hybrid_enabled:
                tasks.append((i, "keyword", q_text, q_vec))
        if graph_documents and query_pairs:
            q_text, q_vec = query_pairs[0]
            tasks.append((0, "graph", q_text, q_vec))
        if query_pairs:
            # Document-table rows the question names by a cell (`_table_rows`).
            tasks.append((0, "rows", *query_pairs[0]))
        # A connected answer's NAMED tools: the same search pinned to each tool,
        # primary query only, in the same pool.
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
            if kind == "rows":
                return (i, task[1]), self._table_rows(
                    org_id, q_text, q_vec, workspace_id=workspace_id, viewer=viewer,
                    tools=[self._source_provider, *(extra_tools or [])], worked=worked,
                )
            if kind == "graph":
                # Graph documents are already limited to the tools this answer may use, so
                # the pin is lifted for this leg only. The viewer still applies.
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
            if results.get((i, "rows")):
                legs.append(results[(i, "rows")])
            if i == 0:
                for tool in extra_tools or []:
                    legs.append(results.get((0, f"vector:{tool}"), []))
                    if self._settings.hybrid_enabled:
                        legs.append(results.get((0, f"keyword:{tool}"), []))
            ranked.append(legs[0] if len(legs) == 1 else self._rrf_fuse(legs, self._settings.rrf_k))
        return ranked

    def _table_rows(
        self, org_id: str, q_text: str, q_vec: list[float], *, workspace_id: str | None,
        viewer: Viewer | None, tools: list[str | None], worked: tuple = (),
    ) -> list[RetrievedChunk]:
        """Rows the question names by a cell, and figures worked out over a table
        (``insights.table_answers``), as passages of their document.

        A sheet embeds only its columns, so rows are found by exact text
        (``rows_naming``) as this viewer. A figure carries its document's cosine;
        a row the better of that and its own text's. Both are real cosines, so the
        gate holds. Never raises.
        """
        try:
            from ..doctables.store import rows_naming

            rows = rows_naming(q_text, org_id=org_id, workspace_id=workspace_id,
                               viewer=viewer, providers=[t for t in tools if t])
            figures = [w for w in worked if w.provider in tools]
            if not rows and not figures:
                return []
            ids = sorted({r.document_id for r in rows} | {w.document_id for w in figures}
                         | {d for w in figures for d in getattr(w, "requires", ())})
            cosine: dict[str, float] = {}
            # Private unless the index says the document is public.
            public: dict[str, bool] = {}
            for hit in self._store.query(org_id, q_vec, top_k=4 * len(ids),
                                         workspace_id=workspace_id, viewer=viewer,
                                         document_ids=ids):
                cosine[hit.document_id] = max(cosine.get(hit.document_id, 0.0), hit.score)
                public[hit.document_id] = hit.doc_is_public
        except Exception:  # noqa: BLE001
            logger.warning("retrieval: table row lookup skipped", exc_info=True)
            return []
        # Re-checked: the cosine query is filtered by this viewer, so an unreadable
        # sheet has no hit and its figures are not shown.
        figures = [f for f in figures if f.document_id in cosine
                   and all(d in cosine for d in getattr(f, "requires", ()))]
        rows = [r for r in rows if r.document_id in cosine]
        own = self._row_cosines(q_vec, [r.text for r in rows])
        return [
            RetrievedChunk(
                content=figure.text, score=cosine.get(figure.document_id, 0.0),
                document_id=figure.document_id, chunk_index=_WORKED_INDEX + i,
                org_id=org_id, document_title=figure.document_title,
                source_provider=figure.provider,
                doc_is_public=public.get(figure.document_id, False),
                source_uri=figure.source_uri,
            )
            for i, figure in enumerate(figures)
        ] + [
            RetrievedChunk(
                content=row.text, score=max(cosine[row.document_id], own[i]),
                document_id=row.document_id, chunk_index=_ROW_INDEX + row.row_no,
                org_id=org_id, document_title=row.document_title,
                source_provider=row.provider, doc_is_public=row.is_public,
                source_uri=row.source_uri,
            )
            for i, row in enumerate(rows)
        ]

    @staticmethod
    def _row_cosines(q_vec: list[float], texts: list[str]) -> list[float]:
        """Cosine of each row's text to the question in one embed call; zeros (the
        document's score stands) when it cannot be measured."""
        if not texts:
            return []
        try:
            from ..agent.routing import _probe_embedder

            vectors = _probe_embedder().embed(texts)
        except Exception:  # noqa: BLE001 - the document's cosine still stands
            logger.warning("retrieval: could not embed named rows", exc_info=True)
            return [0.0] * len(texts)
        qn = sum(x * x for x in q_vec) ** 0.5
        out = []
        for v in vectors:
            vn = sum(x * x for x in v) ** 0.5
            out.append(sum(a * b for a, b in zip(q_vec, v)) / (qn * vn) if qn and vn else 0.0)
        return out

    @staticmethod
    def _rrf_fuse(
        ranked_lists: list[list[RetrievedChunk]], k: int
    ) -> list[RetrievedChunk]:
        """Reciprocal Rank Fusion: score(d) = Σ 1/(k + rank_d). Dedupes by
        (document_id, chunk_index); each chunk keeps its cosine ``score`` for the gate."""
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
    """Keep hits whose rerank score is at least ``ratio`` x the best's. The
    best always stays; off at 0; no-op without positive scores."""
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
    include_ratio: float = 0.0, pool: list[RetrievedChunk] | None = None, always: bool = False,
) -> list[RetrievedChunk]:
    """``top_k`` hits, plus more documents when the evidence is spread out.

    A document is strong when its best rerank score is at least ``ratio`` x
    the top one's. With several strong documents, the usual ``top_k`` is kept
    and passages from other documents down to ``include_ratio`` (from
    ``pool``) are added up to ``max_hits``/``per_doc``: a superset, never
    fewer documents. ``always`` (Deep's extra effort) widens regardless.
    Reranker scores only; the gate is untouched.
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
    if len(strong) <= 1 and not always:
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
    """The document holding the best cosine among ``chunks`` (the gate's). A
    hit with no score is skipped, never trusted."""
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
    """Top ``top_k``, with the best chunk of each OTHER tool guaranteed a seat.

    Otherwise the routed tool fills every slot and the answer says Slack has
    nothing when a thread exists. Swaps replace the routed tool's weakest.
    """
    final = list(ordered[:top_k])
    present = {c.source_provider for c in final}
    swaps = 0
    # One seat per other tool, up to all but two slots (the routed tool keeps two).
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
