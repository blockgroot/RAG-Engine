"""In-process Okapi BM25 for org-scoped keyword ranking.

``ts_rank`` lacks BM25's saturation and length normalization, and BM25
extensions need a different Postgres image (the self-hosted goal runs
stock pgvector). Per-org corpora are small, so ``rank_bm25`` per call is
cheap. RRF fuses the ranks; each row's cosine remains the gate signal.
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
