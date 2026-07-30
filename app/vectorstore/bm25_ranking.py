"""In-process Okapi BM25 for org-scoped keyword ranking (Phase 18).

Why not Postgres ``ts_rank``?
-----------------------------
``ts_rank`` / ``ts_rank_cd`` use a cover-density style score without BM25's
term-frequency saturation or length normalization. Hybrid retrieval only needs
a *rank order* for RRF fusion, but skewed scores still change which chunks enter
the candidate pool when combined with ``LIMIT``.

Why not a Postgres BM25 extension?
------------------------------------
Extensions such as ParadeDB ``pg_search`` or standalone ``pg_bm25`` require a
different Postgres distribution or extra image wiring — a poor fit for the
self-hosted Docker goal (§1) where we already run ``pgvector/pgvector``.

Why in-process BM25 here?
---------------------------
Per-tenant policy corpora are small (hundreds of chunks, not millions). Loading
an org's chunk texts once per ``keyword_search`` call and scoring with
``rank_bm25.BM25Okapi`` is predictable, $0, and needs no new service. RRF still
fuses BM25 ranks with vector ranks; cosine on each row remains the gate signal.

At much larger scale, cache an inverted index per org or move to a dedicated
search backend — out of scope for this phase.
"""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Simple alphanumeric tokenizer (English policy text)."""
    return _TOKEN.findall(text.lower())


def bm25_rank(
    query: str,
    documents: list[str],
    *,
    top_k: int,
) -> list[tuple[int, float]]:
    """Return ``(doc_index, score)`` pairs for the top ``top_k`` documents."""
    if not documents or not query.strip():
        return []
    corpus = [tokenize(d) for d in documents]
    if not any(corpus):
        return []
    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(q_tokens)
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    return [(i, float(s)) for i, s in ranked[:top_k]]
