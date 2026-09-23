"""Hybrid retrieval + reranking (Phase 6), sitting under the Phase 3 gate.

Plain top-k vector search ranks each chunk independently and can leave a genuinely
relevant chunk just outside the cutoff. This retriever addresses that from three
angles at query time:

1. **Hybrid search** — run vector (semantic) *and* keyword (Okapi BM25) search,
   then fuse the two ranked lists with **Reciprocal Rank Fusion (RRF)**. RRF is
   rank-based, so it needs no score normalization between cosine and BM25
   (which live on totally different scales) — the settled default for hybrid RAG.
2. **Cross-encoder reranking** — over-retrieve a wider ``candidate_pool`` then
   rerank it with a cross-encoder, selecting the final ``top_k``.
3. **Recency** — when the question asks about what happened recently
   (``recency_intent.detect_recency``), a third first-stage list searches only
   recently-modified documents, and the final order is fused with a
   newest-first order. Rank-based like everything else here, so it reorders
   and never touches ``gate_score``.

Crucially this only changes *which chunks, in what order* reach the prompt. The
**confidence gate is unchanged**: ``gate_score`` is the best cosine similarity
among candidates (== the vector top-1 the Phase 3 gate always used), so the
pipeline's threshold logic behaves exactly as before.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config.settings import RagSettings, RetrievalSettings
from ..reranker.base import Reranker
from ..vectorstore.base import DateRange, RetrievedChunk, VectorStore, Viewer
from .recency_intent import RecencyIntent


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

    @property
    def recency_enabled(self) -> bool:
        return self._settings.recency_enabled

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
        recency: RecencyIntent | None = None,
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

        ``recency``: the question asked about what happened recently. Adds a
        first-stage leg restricted to recent documents and fuses the final
        order with a newest-first one. Old documents are never EXCLUDED here --
        "the latest leave policy" still wants a policy edited a year ago; an
        explicit window is the caller's hard ``date_range``, not this.
        """
        top_k = self._rag_settings.top_k
        if recency is not None and not self._settings.recency_enabled:
            recency = None
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
            viewer=viewer,
            recent_range=self._recent_range(recency, date_range),
        )

        if len(ranked_lists) == 1:
            candidates = ranked_lists[0]
        else:
            candidates = self._rrf_fuse(ranked_lists, self._settings.rrf_k)

        if not candidates:
            return RetrievalResult(hits=[], gate_score=None)

        gate_score = max((c.score for c in candidates), default=None)

        pool_candidates = candidates[:pool]
        # Newest-first fusion is for a VAGUE recency ask only. An explicit
        # window is already a hard date filter, so inside it relevance decides:
        # fusing by date there demoted an exact-title match out of the top_k
        # because its batch-ingested siblings were a few seconds newer.
        boost = recency is not None and recency.window is None
        # With a boost the reranker orders the WHOLE pool, so the newest-first
        # fusion chooses from every candidate rather than only the reranker's
        # top_k -- otherwise a recent chunk it ranked sixth could never be
        # promoted.
        keep = len(pool_candidates) if boost else top_k
        if self._reranker is not None and self._settings.rerank_enabled:
            ordered = self._reranker.rerank(rerank_q, pool_candidates, keep)
        else:
            ordered = pool_candidates[:keep]
        if boost and ordered:
            # The single most relevant chunk is never displaced by the date
            # boost: "the latest on SYV-6" still has to see SYV-6.
            best, rest = ordered[0], ordered[1:]
            ordered = [best, *self._rrf_fuse([rest, _newest_first(rest)], self._settings.rrf_k)]

        return RetrievalResult(hits=ordered[:top_k], gate_score=gate_score)

    def _recent_range(
        self, recency: RecencyIntent | None, date_range: DateRange | None
    ) -> DateRange | None:
        """The window the recency leg searches, or ``None`` for no such leg.

        An explicit window is used as-is; a vague ask looks back
        ``recency_default_days``. Always intersected with the caller's own
        ``date_range`` -- a leg that searched OUTSIDE a hard filter would
        smuggle excluded documents back in through the fusion.
        """
        if recency is None:
            return None
        window = recency.window or DateRange(
            after=datetime.now(timezone.utc)
            - timedelta(days=self._settings.recency_default_days)
        )
        if date_range is None:
            return window
        afters = [d for d in (window.after, date_range.after) if d is not None]
        befores = [d for d in (window.before, date_range.before) if d is not None]
        return DateRange(
            after=max(afters) if afters else None,
            before=min(befores) if befores else None,
        )

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
        recent_range: DateRange | None = None,
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

        ``recent_range`` adds ONE more vector search, for the primary query
        only, restricted to that window. A recent chunk then appears in two
        lists and RRF lifts it; an old one is still in the others, so nothing
        is excluded. One extra round trip, run concurrently with the rest.
        """
        tasks: list[tuple[int, str, str, list[float]]] = []
        for i, (q_text, q_vec) in enumerate(query_pairs):
            tasks.append((i, "vector", q_text, q_vec))
            if self._settings.hybrid_enabled:
                tasks.append((i, "keyword", q_text, q_vec))
        if recent_range is not None and query_pairs:
            q_text, q_vec = query_pairs[0]
            tasks.append((0, "recent", q_text, q_vec))

        results: dict[tuple[int, str], list[RetrievedChunk]] = {}

        def run(task) -> tuple[tuple[int, str], list[RetrievedChunk]]:
            i, kind, q_text, q_vec = task
            if kind in ("vector", "recent"):
                hits = self._store.query(
                    org_id,
                    q_vec,
                    top_k=pool,
                    workspace_id=workspace_id,
                    source_provider=self._source_provider,
                    date_range=recent_range if kind == "recent" else date_range,
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
                        source_provider=self._source_provider,
                        date_range=date_range,
                        tags=tags,
                        viewer=viewer,
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
            legs = [results.get((i, "vector"), [])]
            if self._settings.hybrid_enabled:
                legs.append(results.get((i, "keyword"), []))
            if (i, "recent") in results:
                legs.append(results[(i, "recent")])
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


def _newest_first(hits: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """``hits`` by the document's own modification date, newest first.

    Undated chunks (a manual ingest) go last rather than being guessed at, and
    the sort is stable so equal dates keep their relevance order.
    """
    return sorted(
        hits,
        key=lambda h: (
            h.last_modified is None,
            -(h.last_modified.timestamp() if h.last_modified is not None else 0.0),
        ),
    )
