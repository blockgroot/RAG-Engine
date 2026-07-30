"""BM25 keyword ranking vs legacy ts_rank ordering (Phase 18)."""

from __future__ import annotations

import uuid

import numpy as np

from app.db.connection import get_connection
from app.vectorstore.bm25_ranking import bm25_rank
from .conftest import requires_db

WELLNESS_DOC = (
    "Leave wellness allowance covers health-related products and supplements "
    "including protein powder when prescribed."
)
TRAVEL_DOC = "Business travel expenses are reimbursed up to five hundred dollars per trip."
DISTRACTOR = "Fire drills occur twice per year in spring and autumn seasons."


def _ts_rank_order(org_id: str, query: str, limit: int) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT content
            FROM chunks
            WHERE org_id = %s::uuid
              AND content_tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY ts_rank(content_tsv, websearch_to_tsquery('english', %s)) DESC
            LIMIT %s
            """,
            (org_id, query, query, limit),
        ).fetchall()
    return [r[0] for r in rows]


@requires_db
def test_bm25_promotes_wellness_doc_for_supplement_query(store, embedder, org_cleanup):
    org_id = store.create_organization(f"BM25-{uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    for title, text in [
        ("wellness", WELLNESS_DOC),
        ("travel", TRAVEL_DOC),
        ("noise", DISTRACTOR),
    ]:
        store.add_document(org_id, title, [text], embedder.embed([text]))

    query = "wellness supplements protein allowance"
    qvec = embedder.embed([query])[0]

    ts_hits = _ts_rank_order(org_id, query, 3)
    bm25_hits = store.keyword_search(org_id, query, qvec, top_k=3)
    assert ts_hits, "ts_rank returned nothing — fixture broken"
    assert bm25_hits, "BM25 keyword_search returned nothing"

    # Wellness doc must be top-1 under BM25 for this vocabulary-mismatch-style query.
    assert "wellness" in bm25_hits[0].content.lower()
    assert "supplement" in bm25_hits[0].content.lower()

    # Document that BM25 and ts_rank can differ (ordering); both should still find wellness.
    assert any("wellness" in h.lower() for h in ts_hits)


def test_bm25_rank_unit():
    docs = [WELLNESS_DOC, TRAVEL_DOC, DISTRACTOR]
    ranked = bm25_rank("protein supplements wellness", docs, top_k=2)
    assert ranked[0][0] == 0
