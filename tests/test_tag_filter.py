"""Hard metadata filter: department/tag on retrieval (production-RAG gap #3,
the tag half — date-range shipped first).

``tags`` is a hard filter on ``documents.tags``, threaded through
``HybridRetriever`` and ``QueryAnswerCache`` exactly like the existing
``date_range``/``workspace_id``/``source_provider`` filters — a ``WHERE``
term (overlap match), never a re-ranking signal, no-op when ``None``. This
module deliberately never decides WHERE a tag comes from (admin-set field,
source metadata, ...) — that's for whoever calls ``add_document``/
``upsert_source_document`` with a value; this is pure plumbing.

Plumbing tests only (no live Postgres): they prove the filter reaches the
store call and that the cache never conflates two different tag sets. The
SQL itself (``pgvector_store.py``) mirrors the already-tested ``date_range``
WHERE-clause shape.
"""

from __future__ import annotations

from app.rag.query_cache import _question_hash
from app.rag.retrieval import HybridRetriever
from app.vectorstore.base import RetrievedChunk, VectorStore


class _RecordingStore(VectorStore):
    def __init__(self) -> None:
        self.query_tags: list[list[str] | None] = []
        self.keyword_tags: list[list[str] | None] = []

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
    ):
        self.query_tags.append(tags)
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
    ):
        self.keyword_tags.append(tags)
        return []


def test_retrieve_passes_tags_to_both_vector_and_keyword_legs():
    store = _RecordingStore()
    retriever = HybridRetriever(store, reranker=None)

    retriever.retrieve("org-1", "policy question", [1.0], tags=["engineering"])

    assert store.query_tags == [["engineering"]]
    assert store.keyword_tags == [["engineering"]]


def test_retrieve_with_no_tags_is_unaffected():
    store = _RecordingStore()
    retriever = HybridRetriever(store, reranker=None)

    retriever.retrieve("org-1", "policy question", [1.0])

    assert store.query_tags == [None]
    assert store.keyword_tags == [None]


def test_question_hash_differs_by_tags():
    plain = _question_hash("what is the leave policy")
    eng = _question_hash("what is the leave policy", tags=["engineering"])
    hr = _question_hash("what is the leave policy", tags=["hr"])

    assert plain != eng
    assert eng != hr


def test_question_hash_is_order_independent_for_tags():
    """tags is an OR-match filter — [a, b] and [b, a] mean the same query,
    so they must share a cache slot rather than doubling storage."""
    a = _question_hash("q", tags=["engineering", "hr"])
    b = _question_hash("q", tags=["hr", "engineering"])
    assert a == b


def test_question_hash_unchanged_when_tags_is_none_or_empty():
    """Every pre-existing cache key must stay byte-identical."""
    base = _question_hash("q")
    assert base == _question_hash("q", tags=None)
    assert base == _question_hash("q", tags=[])
