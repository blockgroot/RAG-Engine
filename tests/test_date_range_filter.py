"""Hard metadata filter: date-range on retrieval (production-RAG gap #3).

A ``DateRange`` (``app/vectorstore/base.py``) is a hard filter on
``documents.source_last_modified``, threaded through ``HybridRetriever``
exactly like the existing ``workspace_id``/``source_provider``
filters — a ``WHERE`` term, never a re-ranking signal, no-op when ``None``.

These are plumbing tests (no live Postgres): they prove the filter actually
reaches the store call. The SQL itself (``pgvector_store.py``) is exercised indirectly by the
existing ``requires_db`` retrieval tests, which all still pass unfiltered.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.rag.retrieval import HybridRetriever
from app.vectorstore.base import DateRange, RetrievedChunk, VectorStore


class _RecordingStore(VectorStore):
    def __init__(self) -> None:
        self.query_date_ranges: list[DateRange | None] = []
        self.keyword_date_ranges: list[DateRange | None] = []

    def create_organization(self, name: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def add_document(self, *a, **kw) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def query(
        self,
        org_id,
        query_embedding,
        top_k=5,
        workspace_id=None,
        source_provider=None,
        date_range=None,
        tags=None,
        viewer=None,
    ):
        self.query_date_ranges.append(date_range)
        return [
            RetrievedChunk(
                content="c", score=0.9, document_id="d1", chunk_index=0, org_id=org_id
            )
        ]

    def keyword_search(
        self,
        org_id,
        query_text,
        query_embedding,
        top_k=30,
        workspace_id=None,
        source_provider=None,
        date_range=None,
        tags=None,
        viewer=None,
    ):
        self.keyword_date_ranges.append(date_range)
        return []


def test_retrieve_passes_date_range_to_both_vector_and_keyword_legs():
    store = _RecordingStore()
    retriever = HybridRetriever(store, reranker=None)
    date_range = DateRange(after=datetime(2026, 1, 1, tzinfo=timezone.utc))

    retriever.retrieve("org-1", "policy question", [1.0], date_range=date_range)

    assert store.query_date_ranges == [date_range]
    assert store.keyword_date_ranges == [date_range]


def test_retrieve_with_no_date_range_is_unaffected():
    store = _RecordingStore()
    retriever = HybridRetriever(store, reranker=None)

    retriever.retrieve("org-1", "policy question", [1.0])

    assert store.query_date_ranges == [None]
    assert store.keyword_date_ranges == [None]

