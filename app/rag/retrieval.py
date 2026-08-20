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

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ..config.settings import RagSettings, RetrievalSettings
from ..reranker.base import Reranker
from ..vectorstore.base import DateRange, RetrievedChunk, VectorStore


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
    ) -> None:
        self._store = store
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
        """
        top_k = self._rag_settings.top_k
        pool = self._settings.candidate_pool
        rerank_q = rerank_query or query_text

        query_pairs = [(query_text, query_embedding)]
        if extra_queries:
            query_pairs.extend(extra_queries)

        ranked_lists = self._first_stage_all(
            org_id,
            query_pairs,
            pool,
            workspace_id=workspace_id,
            date_range=date_range,
            tags=tags,
        )

        if len(ranked_lists) == 1:
            candidates = ranked_lists[0]
        else:
            candidates = self._rrf_fuse(ranked_lists, self._settings.rrf_k)

        if not candidates:
            return RetrievalResult(hits=[], gate_score=None)

        gate_score = max((c.score for c in candidates), default=None)

        pool_candidates = candidates[:pool]
        if self._reranker is not None and self._settings.rerank_enabled:
            final = self._reranker.rerank(rerank_q, pool_candidates, top_k)
        else:
            final = pool_candidates[:top_k]

        return RetrievalResult(hits=final, gate_score=gate_score)

    def _first_stage_all(
        self,
        org_id: str,
        query_pairs: list[tuple[str, list[float]]],
        pool: int,
        *,
        workspace_id: str | None = None,
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
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
        """
        tasks: list[tuple[int, str, str, list[float]]] = []
        for i, (q_text, q_vec) in enumerate(query_pairs):
            tasks.append((i, "vector", q_text, q_vec))
            if self._settings.hybrid_enabled:
                tasks.append((i, "keyword", q_text, q_vec))

        results: dict[tuple[int, str], list[RetrievedChunk]] = {}

        def run(task) -> tuple[tuple[int, str], list[RetrievedChunk]]:
            i, kind, q_text, q_vec = task
            if kind == "vector":
                hits = self._store.query(
                    org_id,
                    q_vec,
                    top_k=pool,
                    workspace_id=workspace_id,
                    source_provider=self._source_provider,
                    date_range=date_range,
                    tags=tags,
                )
            else:
                try:
                    hits = self._store.keyword_search(
                        org_id,
                        q_text,
                        q_vec,
                        top_k=pool,
                        workspace_id=workspace_id,
                        source_provider=self._source_provider,
                        date_range=date_range,
                        tags=tags,
                    )
                except NotImplementedError:
                    hits = []
            return (i, kind), list(hits)

        if len(tasks) == 1:
            key, hits = run(tasks[0])
            results[key] = hits
        else:
            workers = min(len(tasks), _MAX_RETRIEVAL_WORKERS)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for key, hits in ex.map(run, tasks):
                    results[key] = hits

        ranked: list[list[RetrievedChunk]] = []
        for i in range(len(query_pairs)):
            vec_hits = results.get((i, "vector"), [])
            if self._settings.hybrid_enabled:
                kw_hits = results.get((i, "keyword"), [])
                ranked.append(self._rrf_fuse([vec_hits, kw_hits], self._settings.rrf_k))
            else:
                ranked.append(vec_hits)
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
