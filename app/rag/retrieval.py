"""Hybrid retrieval + reranking (Phase 6), sitting under the Phase 3 gate.

Plain top-k vector search ranks each chunk independently and can leave a genuinely
relevant chunk just outside the cutoff. This retriever addresses that from two
angles at query time:

1. **Hybrid search** — run vector (semantic) *and* keyword (BM25-style) search,
   then fuse the two ranked lists with **Reciprocal Rank Fusion (RRF)**. RRF is
   rank-based, so it needs no score normalization between cosine and ts_rank
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
        self, org_id: str, query_text: str, query_embedding: list[float]
    ) -> RetrievalResult:
        top_k = self._rag_settings.top_k
        pool = self._settings.candidate_pool

        # First-stage vector recall (wide).
        vec_hits = self._store.query(org_id, query_embedding, top_k=pool)

        # Hybrid: add keyword recall and fuse by rank.
        if self._settings.hybrid_enabled:
            try:
                kw_hits = self._store.keyword_search(
                    org_id, query_text, query_embedding, top_k=pool
                )
            except NotImplementedError:
                kw_hits = []
            candidates = self._rrf_fuse([vec_hits, kw_hits], self._settings.rrf_k)
        else:
            candidates = list(vec_hits)

        if not candidates:
            return RetrievalResult(hits=[], gate_score=None)

        # Gate signal = best cosine among candidates (== vector top-1). Unchanged
        # from Phase 3, so the confidence gate downstream needs no recalibration.
        gate_score = max((c.score for c in candidates), default=None)

        pool_candidates = candidates[:pool]
        if self._reranker is not None and self._settings.rerank_enabled:
            final = self._reranker.rerank(query_text, pool_candidates, top_k)
        else:
            final = pool_candidates[:top_k]

        return RetrievalResult(hits=final, gate_score=gate_score)

    def retrieve_expanded(
        self,
        org_id: str,
        primary_query_text: str,
        queries: list[tuple[str, list[float]]],
    ) -> RetrievalResult:
        """Retrieve using MULTIPLE query variants (Phase 10: vocabulary expansion).

        ``queries`` is ``[(text, embedding), ...]`` — typically the normalized
        query first, followed by alternate phrasings from ``QueryUnderstander``.
        Each variant runs its own vector (+ keyword, if enabled) search; ALL
        result lists are fused together with the SAME Reciprocal Rank Fusion used
        by ``retrieve()`` (it already generalizes to any number of ranked lists —
        no change needed there), so a chunk that only one phrasing's vocabulary
        matches still has a chance to surface. Reranking then runs against
        ``primary_query_text`` only (a cross-encoder needs one canonical query),
        so expansions only widen RECALL — they never change what "relevant"
        means for the final ranking.

        Falls back to plain ``retrieve()`` when only one query variant is given,
        so callers don't pay any fan-out cost on a single-query call.
        """
        if len(queries) <= 1:
            text, vec = queries[0] if queries else (primary_query_text, [])
            return self.retrieve(org_id, text, vec)

        top_k = self._rag_settings.top_k
        pool = self._settings.candidate_pool
        # Bound the per-query fan-out so total DB round trips stay predictable
        # regardless of how many expansions were generated.
        per_query_pool = max(pool // len(queries), 10)

        ranked_lists: list[list[RetrievedChunk]] = []
        for text, vec in queries:
            ranked_lists.append(self._store.query(org_id, vec, top_k=per_query_pool))
            if self._settings.hybrid_enabled:
                try:
                    ranked_lists.append(
                        self._store.keyword_search(org_id, text, vec, top_k=per_query_pool)
                    )
                except NotImplementedError:
                    pass

        candidates = self._rrf_fuse(ranked_lists, self._settings.rrf_k)
        if not candidates:
            return RetrievalResult(hits=[], gate_score=None)

        gate_score = max(c.score for c in candidates)
        pool_candidates = candidates[:pool]
        if self._reranker is not None and self._settings.rerank_enabled:
            final = self._reranker.rerank(primary_query_text, pool_candidates, top_k)
        else:
            final = pool_candidates[:top_k]

        return RetrievalResult(hits=final, gate_score=gate_score)

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
