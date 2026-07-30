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

from dataclasses import dataclass

from ..config.settings import RagSettings, RetrievalSettings
from ..reranker.base import Reranker
from ..vectorstore.base import RetrievedChunk, VectorStore


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
    ) -> None:
        self._store = store
        self._reranker = reranker
        self._settings = settings or RetrievalSettings.from_env()
        self._rag_settings = rag_settings or RagSettings.from_env()

    def retrieve(
        self,
        org_id: str,
        query_text: str,
        query_embedding: list[float],
        *,
        extra_queries: list[tuple[str, list[float]]] | None = None,
        rerank_query: str | None = None,
    ) -> RetrievalResult:
        """Retrieve for ``query_text``; optionally fuse extra (sub-)queries first."""
        top_k = self._rag_settings.top_k
        pool = self._settings.candidate_pool
        rerank_q = rerank_query or query_text

        query_pairs = [(query_text, query_embedding)]
        if extra_queries:
            query_pairs.extend(extra_queries)

        ranked_lists: list[list[RetrievedChunk]] = []
        for q_text, q_vec in query_pairs:
            ranked_lists.append(self._first_stage(org_id, q_text, q_vec, pool))

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

    def _first_stage(
        self, org_id: str, query_text: str, query_embedding: list[float], pool: int
    ) -> list[RetrievedChunk]:
        vec_hits = self._store.query(org_id, query_embedding, top_k=pool)

        if self._settings.hybrid_enabled:
            try:
                kw_hits = self._store.keyword_search(
                    org_id, query_text, query_embedding, top_k=pool
                )
            except NotImplementedError:
                kw_hits = []
            return self._rrf_fuse([vec_hits, kw_hits], self._settings.rrf_k)
        return list(vec_hits)

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
