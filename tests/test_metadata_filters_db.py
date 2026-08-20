"""Hard metadata filters against the real store (production-RAG gap #3).

``date_range`` and ``tags`` are both WHERE-clause filters on ``documents``,
applied identically on the vector and keyword legs. The plumbing (does the
filter reach the store call) is proven with fakes in
``test_date_range_filter.py``/``test_tag_filter.py``; this file proves the
actual SQL against a real Postgres — a chunk outside the filter must never
come back, regardless of how well it matches semantically.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.ingestion.pipeline import ingest_source
from app.vectorstore.base import DateRange
from .conftest import requires_db

LEAVE_TEXT = "Full-time employees receive 25 days of paid annual leave per year."


def _seed_tagged(store, embedder, org_cleanup, rows):
    """``rows``: list of (text, tags, last_modified)."""
    org_id = store.create_organization(f"MetaFilter Co-{uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    for i, (text, tags, last_modified) in enumerate(rows):
        store.upsert_source_document(
            org_id,
            provider="notion",
            external_id=f"page-{i}",
            title=f"doc{i}",
            chunks=[text],
            embeddings=embedder.embed([text]),
            last_modified=last_modified,
            tags=tags,
        )
    return org_id


@requires_db
def test_tags_filter_excludes_non_matching_documents(store, embedder, org_cleanup):
    org_id = _seed_tagged(
        store,
        embedder,
        org_cleanup,
        [
            (LEAVE_TEXT, ["engineering"], None),
            (LEAVE_TEXT, ["sales"], None),
        ],
    )
    qvec = embedder.embed([LEAVE_TEXT])[0]

    hits = store.query(org_id, qvec, top_k=10, tags=["engineering"])

    assert len(hits) == 1

    kw_hits = store.keyword_search(org_id, "annual leave", qvec, top_k=10, tags=["engineering"])
    assert len(kw_hits) == 1


@requires_db
def test_tags_filter_matches_on_any_overlap(store, embedder, org_cleanup):
    """OR semantics: a document tagged [hr, engineering] matches a
    single-tag OR a multi-tag request that overlaps it at all."""
    org_id = _seed_tagged(
        store,
        embedder,
        org_cleanup,
        [(LEAVE_TEXT, ["hr", "engineering"], None)],
    )
    qvec = embedder.embed([LEAVE_TEXT])[0]

    assert len(store.query(org_id, qvec, top_k=10, tags=["engineering"])) == 1
    assert len(store.query(org_id, qvec, top_k=10, tags=["sales", "hr"])) == 1
    assert len(store.query(org_id, qvec, top_k=10, tags=["sales", "marketing"])) == 0


@requires_db
def test_no_tags_filter_is_a_no_op(store, embedder, org_cleanup):
    org_id = _seed_tagged(
        store,
        embedder,
        org_cleanup,
        [(LEAVE_TEXT, ["engineering"], None), (LEAVE_TEXT, None, None)],
    )
    qvec = embedder.embed([LEAVE_TEXT])[0]

    assert len(store.query(org_id, qvec, top_k=10)) == 2


@requires_db
def test_date_range_filter_excludes_documents_outside_the_window(store, embedder, org_cleanup):
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    old = now - timedelta(days=400)
    org_id = _seed_tagged(
        store,
        embedder,
        org_cleanup,
        [(LEAVE_TEXT, None, now), (LEAVE_TEXT, None, old)],
    )
    qvec = embedder.embed([LEAVE_TEXT])[0]

    recent_only = DateRange(after=now - timedelta(days=30))
    hits = store.query(org_id, qvec, top_k=10, date_range=recent_only)
    assert len(hits) == 1

    kw_hits = store.keyword_search(
        org_id, "annual leave", qvec, top_k=10, date_range=recent_only
    )
    assert len(kw_hits) == 1


@requires_db
def test_date_range_and_tags_combine_as_and(store, embedder, org_cleanup):
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    old = now - timedelta(days=400)
    org_id = _seed_tagged(
        store,
        embedder,
        org_cleanup,
        [
            (LEAVE_TEXT, ["engineering"], now),   # matches both filters
            (LEAVE_TEXT, ["engineering"], old),   # right tag, wrong date
            (LEAVE_TEXT, ["sales"], now),         # right date, wrong tag
        ],
    )
    qvec = embedder.embed([LEAVE_TEXT])[0]

    hits = store.query(
        org_id,
        qvec,
        top_k=10,
        tags=["engineering"],
        date_range=DateRange(after=now - timedelta(days=30)),
    )
    assert len(hits) == 1


class _OneDocAdapter:
    """Duck-typed ``SourceAdapter`` yielding a single fixed document."""

    def __init__(self, external_id: str, text: str) -> None:
        self._external_id = external_id
        self._text = text

    def list_documents(self):
        from app.sources.base import SourceRef

        return [SourceRef(self._external_id, "Doc", last_modified=None)]

    def fetch_document(self, external_id: str):
        from app.sources.base import SourceDocument

        return SourceDocument(
            external_id=self._external_id,
            title="Doc",
            content=self._text,
            source_uri=None,
            last_modified=None,
        )

    def get_last_modified(self, external_id: str):
        return None


@requires_db
def test_ingest_source_tags_flow_through_to_the_stored_document(store, embedder, org_cleanup):
    """The tag a caller passes at ingest time must be queryable afterwards —
    proves the end-to-end path, not just the store-level SQL in isolation."""
    org_id = store.create_organization(f"IngestTags Co-{uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    external_id = f"page-{uuid.uuid4().hex[:8]}"

    ingest_source(
        _OneDocAdapter(external_id, LEAVE_TEXT),
        org_id,
        provider="notion",
        embedder=embedder,
        store=store,
        contextual=SimpleNamespace(enabled=False),
        keywords=SimpleNamespace(enabled=False),
        tags=["engineering"],
    )

    qvec = embedder.embed([LEAVE_TEXT])[0]
    assert len(store.query(org_id, qvec, top_k=10, tags=["engineering"])) == 1
    assert len(store.query(org_id, qvec, top_k=10, tags=["sales"])) == 0
