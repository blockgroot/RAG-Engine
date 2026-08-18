"""Postgres + pgvector implementation of the ``VectorStore`` interface.

Every read and write is scoped by ``org_id`` — inserts stamp it on every row,
and ``query`` filters ``WHERE org_id = ...`` *before* ranking. That WHERE clause,
not any index, is what guarantees tenant isolation.
"""

from __future__ import annotations

import numpy as np
from pgvector import Vector

from ..config.settings import DatabaseSettings
from ..core.exceptions import EmbeddingProviderError, ProviderError
from ..db.connection import get_connection
from datetime import datetime

from .base import OrganizationRef, RetrievedChunk, StoredSourceDocument, VectorStore
from .bm25_ranking import bm25_rank

def _to_db_vector(embedding: list[float] | np.ndarray) -> Vector:
    """Bind an embedding so psycopg dumps it as pgvector ``vector``, not ndarray.

    ``register_vector`` makes ``Vector`` dump correctly; a raw ``np.ndarray``
    still fails with ``cannot adapt type 'ndarray'`` when adapters are missing
    or when some cursor paths skip them — wrapping is the reliable path.
    """
    arr = np.asarray(embedding, dtype=np.float32).reshape(-1)
    if arr.size == 0:
        raise EmbeddingProviderError("embedding is empty")
    return Vector(arr.tolist())


class PgVectorStore(VectorStore):
    """Tenant-scoped chunk store backed by Postgres/pgvector."""

    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self._settings = settings or DatabaseSettings.from_env()

    def create_organization(self, name: str) -> str:
        with get_connection(self._settings) as conn:
            row = conn.execute(
                "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
                (name,),
            ).fetchone()
        return str(row[0])

    def list_organizations(self) -> list[OrganizationRef]:
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                SELECT o.id::text, o.name, count(DISTINCT d.id) AS docs
                FROM organizations o
                LEFT JOIN documents d ON d.org_id = o.id
                GROUP BY o.id, o.name, o.created_at
                ORDER BY o.created_at DESC
                """
            ).fetchall()
        return [OrganizationRef(id=r[0], name=r[1], document_count=int(r[2])) for r in rows]

    def add_document(
        self,
        org_id: str,
        title: str,
        chunks: list[str],
        embeddings: list[list[float]],
        source_uri: str | None = None,
        workspace_id: str | None = None,
    ) -> str:
        if len(chunks) != len(embeddings):
            raise ProviderError(
                f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) "
                "must be the same length"
            )
        if not chunks:
            raise ProviderError("Cannot add a document with no chunks")

        with get_connection(self._settings) as conn:
            doc_row = conn.execute(
                """
                INSERT INTO documents (org_id, title, source_uri, workspace_id)
                VALUES (%s::uuid, %s, %s, %s::uuid)
                RETURNING id
                """,
                (org_id, title, source_uri, workspace_id),
            ).fetchone()
            document_id = doc_row[0]

            rows = [
                (
                    org_id,
                    document_id,
                    index,
                    content,
                    _to_db_vector(embedding),
                    workspace_id,
                )
                for index, (content, embedding) in enumerate(zip(chunks, embeddings))
            ]
            conn.cursor().executemany(
                """
                INSERT INTO chunks (org_id, document_id, chunk_index, content, embedding, workspace_id)
                VALUES (%s::uuid, %s, %s, %s, %s, %s::uuid)
                """,
                rows,
            )

        return str(document_id)

    def query(
        self,
        org_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        workspace_id: str | None = None,
        source_provider: str | None = None,
    ) -> list[RetrievedChunk]:
        if not query_embedding:
            raise EmbeddingProviderError("query_embedding is empty")

        vector = _to_db_vector(query_embedding)
        # ``documents`` is already LEFT JOINed for the title, so the provider
        # filter is a WHERE term rather than a second join. With NULL the term
        # is a no-op and the plan is unchanged from before this parameter
        # existed; with a value the LEFT JOIN behaves as an inner one, which is
        # correct — a chunk with no document row has no provider to match.
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                SELECT c.content,
                       1 - (c.embedding <=> %s) AS score,
                       c.document_id::text,
                       c.chunk_index,
                       c.org_id::text,
                       d.title
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.org_id = %s::uuid
                  AND c.workspace_id IS NOT DISTINCT FROM %s::uuid
                  AND (%s::text IS NULL OR d.source_provider = %s::text)
                ORDER BY c.embedding <=> %s
                LIMIT %s
                """,
                (
                    vector,
                    org_id,
                    workspace_id,
                    source_provider,
                    source_provider,
                    vector,
                    top_k,
                ),
            ).fetchall()

        return [
            RetrievedChunk(
                content=row[0],
                score=float(row[1]),
                document_id=row[2],
                chunk_index=row[3],
                org_id=row[4],
                document_title=(str(row[5]).strip() if row[5] else None),
            )
            for row in rows
        ]

    def list_chunk_texts(self, org_id: str) -> list[str]:
        """All chunk texts for ``org_id`` — corpus vocabulary for query spelling."""
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                "SELECT content FROM chunks WHERE org_id = %s::uuid",
                (org_id,),
            ).fetchall()
        return [row[0] for row in rows if row[0]]

    def keyword_search(
        self,
        org_id: str,
        query_text: str,
        query_embedding: list[float],
        top_k: int = 30,
        workspace_id: str | None = None,
        source_provider: str | None = None,
    ) -> list[RetrievedChunk]:
        """Full-text keyword search within ``org_id`` (Phase 6 hybrid retrieval).

        Phase 18: ranks matching chunks with in-process Okapi BM25 (see
        ``bm25_ranking.py``) instead of Postgres ``ts_rank``. Each row still
        carries cosine similarity vs ``query_embedding`` in ``score`` for the gate.

        **Bounded candidate set.** This query used to have no ``LIMIT``: a common
        term matched *every* such chunk, and for each one Postgres computed a
        cosine distance, joined ``documents``, and shipped the full text back —
        all to keep ``top_k`` (30) of them. Measured on a 400-chunk corpus, the
        term "leave" pulled 160 rows to return 30, and that ratio grows linearly
        with the corpus, so it is a scaling cliff rather than a constant cost.
        Now Postgres orders by ``ts_rank`` and keeps the best
        ``keyword_candidate_limit`` rows, and the expensive per-row work (cosine,
        title join, content transfer) happens only for the survivors.

        **Honest caveat:** on a corpus where a term matches more than the limit,
        BM25 now ranks the top-N by ``ts_rank`` rather than every match, and its
        IDF is computed over that subset. The default is set high enough to be a
        no-op at realistic corpus sizes — where it *does* bite, the old
        behaviour was pathological anyway.
        """
        if not query_embedding:
            raise EmbeddingProviderError("query_embedding is empty")
        if not query_text.strip():
            return []

        vector = _to_db_vector(query_embedding)
        # The candidate CTE deliberately touches only ``chunks`` — that is what
        # keeps the ts_rank cut cheap before the per-row cosine/title work. A
        # provider filter needs ``documents``, so the join is added ONLY when
        # filtering; the unfiltered query stays byte-identical to before, rather
        # than carrying a join the planner may or may not optimise away on the
        # hot path every question takes.
        provider_join = (
            "JOIN documents fd ON fd.id = c.document_id AND fd.source_provider = %s::text"
            if source_provider is not None
            else ""
        )
        params: list = [org_id, workspace_id]
        if source_provider is not None:
            params.insert(0, source_provider)
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                f"""
                WITH matched AS (
                    SELECT c.content,
                           c.document_id,
                           c.chunk_index,
                           c.org_id,
                           c.embedding
                    FROM chunks c
                    {provider_join}
                    WHERE c.org_id = %s::uuid
                      AND c.workspace_id IS NOT DISTINCT FROM %s::uuid
                      AND c.content_tsv @@ websearch_to_tsquery('english', %s)
                    ORDER BY ts_rank(
                        c.content_tsv, websearch_to_tsquery('english', %s)
                    ) DESC
                    LIMIT %s
                )
                SELECT m.content,
                       m.document_id::text,
                       m.chunk_index,
                       m.org_id::text,
                       1 - (m.embedding <=> %s) AS score,
                       d.title
                FROM matched m
                LEFT JOIN documents d ON d.id = m.document_id
                """,
                (
                    *params,
                    query_text,
                    query_text,
                    self._settings.keyword_candidate_limit,
                    vector,
                ),
            ).fetchall()

        if not rows:
            return []

        contents = [r[0] for r in rows]
        bm25_hits = bm25_rank(query_text, contents, top_k=min(top_k, len(contents)))
        if not bm25_hits:
            return []

        out: list[RetrievedChunk] = []
        for doc_idx, _bm25_score in bm25_hits:
            row = rows[doc_idx]
            out.append(
                RetrievedChunk(
                    content=row[0],
                    score=float(row[4]),
                    document_id=row[1],
                    chunk_index=row[2],
                    org_id=row[3],
                    document_title=(str(row[5]).strip() if row[5] else None),
                )
            )
        return out

    def recent_chunks(
        self,
        org_id: str,
        provider: str,
        *,
        workspace_id: str | None = None,
        limit: int = 40,
    ) -> list[RetrievedChunk]:
        """Newest chunks first, by the document's own source timestamp.

        Orders on ``source_last_modified`` (when the thread last had a reply)
        rather than ``documents.created_at`` (when we happened to ingest it) —
        a backfill ingests months of history in one run, so ingest order says
        nothing about what is recent to the reader. ``created_at`` breaks ties
        for rows a source gave no timestamp for.

        ``chunk_index`` ascending within a document keeps a multi-chunk thread
        in reading order, so the summarizer sees a conversation rather than
        shuffled fragments.
        """
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                SELECT c.content, c.document_id::text, c.chunk_index,
                       c.org_id::text, d.title, d.source_external_id
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.org_id = %s::uuid
                  AND c.workspace_id IS NOT DISTINCT FROM %s::uuid
                  AND d.source_provider = %s
                ORDER BY d.source_last_modified DESC NULLS LAST,
                         d.created_at DESC,
                         c.chunk_index ASC
                LIMIT %s
                """,
                (org_id, workspace_id, provider, limit),
            ).fetchall()
        return [
            RetrievedChunk(
                content=r[0],
                score=0.0,
                document_id=r[1],
                chunk_index=r[2],
                org_id=r[3],
                document_title=(str(r[4]).strip() if r[4] else None),
                source_external_id=(str(r[5]).strip() if r[5] else None),
            )
            for r in rows
        ]

    def list_source_documents(
        self, org_id: str, provider: str, workspace_id: str | None = None
    ) -> list[StoredSourceDocument]:
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                SELECT id::text, source_provider, source_external_id, title,
                       source_uri, source_last_modified
                FROM documents
                WHERE org_id = %s::uuid
                  AND source_provider = %s
                  AND workspace_id IS NOT DISTINCT FROM %s::uuid
                  AND source_external_id IS NOT NULL
                """,
                (org_id, provider, workspace_id),
            ).fetchall()
        return [
            StoredSourceDocument(
                document_id=r[0],
                provider=r[1],
                external_id=r[2],
                title=r[3],
                source_uri=r[4],
                last_modified=r[5],
            )
            for r in rows
        ]

    def upsert_source_document(
        self,
        org_id: str,
        *,
        provider: str,
        external_id: str,
        title: str,
        chunks: list[str],
        embeddings: list[list[float]],
        source_uri: str | None = None,
        last_modified: datetime | None = None,
        workspace_id: str | None = None,
    ) -> str:
        if len(chunks) != len(embeddings):
            raise ProviderError(
                f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) "
                "must be the same length"
            )
        if not chunks:
            raise ProviderError("Cannot add a document with no chunks")
        if not external_id:
            raise ProviderError("external_id is required for upsert_source_document")

        with get_connection(self._settings) as conn:
            # Drop prior copy of this page + legacy URI duplicates (no external id),
            # scoped to this provider AND workspace so another provider's rows, or
            # another workspace's/the org-wide connection's rows for the same
            # provider, are never touched.
            if source_uri:
                conn.execute(
                    """
                    DELETE FROM documents
                    WHERE org_id = %s::uuid
                      AND source_provider = %s
                      AND workspace_id IS NOT DISTINCT FROM %s::uuid
                      AND (
                        source_external_id = %s
                        OR (source_uri = %s AND source_external_id IS NULL)
                      )
                    """,
                    (org_id, provider, workspace_id, external_id, source_uri),
                )
            else:
                conn.execute(
                    """
                    DELETE FROM documents
                    WHERE org_id = %s::uuid
                      AND source_provider = %s
                      AND workspace_id IS NOT DISTINCT FROM %s::uuid
                      AND source_external_id = %s
                    """,
                    (org_id, provider, workspace_id, external_id),
                )

            doc_row = conn.execute(
                """
                INSERT INTO documents (
                    org_id, title, source_uri, source_provider, source_external_id,
                    source_last_modified, workspace_id
                )
                VALUES (%s::uuid, %s, %s, %s, %s, %s, %s::uuid)
                RETURNING id
                """,
                (org_id, title, source_uri, provider, external_id, last_modified, workspace_id),
            ).fetchone()
            document_id = doc_row[0]

            rows = [
                (
                    org_id,
                    document_id,
                    index,
                    content,
                    _to_db_vector(embedding),
                    workspace_id,
                )
                for index, (content, embedding) in enumerate(zip(chunks, embeddings))
            ]
            conn.cursor().executemany(
                """
                INSERT INTO chunks (org_id, document_id, chunk_index, content, embedding, workspace_id)
                VALUES (%s::uuid, %s, %s, %s, %s, %s::uuid)
                """,
                rows,
            )

        return str(document_id)

    def acknowledge_source_document(
        self,
        org_id: str,
        *,
        provider: str,
        external_id: str,
        title: str,
        source_uri: str | None = None,
        last_modified: datetime | None = None,
        workspace_id: str | None = None,
    ) -> str:
        """Upsert metadata-only row so empty pages are not forever "new"."""
        if not external_id:
            raise ProviderError("external_id is required for acknowledge_source_document")
        with get_connection(self._settings) as conn:
            if source_uri:
                conn.execute(
                    """
                    DELETE FROM documents
                    WHERE org_id = %s::uuid
                      AND source_provider = %s
                      AND workspace_id IS NOT DISTINCT FROM %s::uuid
                      AND (
                        source_external_id = %s
                        OR (source_uri = %s AND source_external_id IS NULL)
                      )
                    """,
                    (org_id, provider, workspace_id, external_id, source_uri),
                )
            else:
                conn.execute(
                    """
                    DELETE FROM documents
                    WHERE org_id = %s::uuid
                      AND source_provider = %s
                      AND workspace_id IS NOT DISTINCT FROM %s::uuid
                      AND source_external_id = %s
                    """,
                    (org_id, provider, workspace_id, external_id),
                )
            row = conn.execute(
                """
                INSERT INTO documents (
                    org_id, title, source_uri, source_provider, source_external_id,
                    source_last_modified, workspace_id
                )
                VALUES (%s::uuid, %s, %s, %s, %s, %s, %s::uuid)
                RETURNING id
                """,
                (org_id, title, source_uri, provider, external_id, last_modified, workspace_id),
            ).fetchone()
        return str(row[0])

    def delete_source_documents(
        self,
        org_id: str,
        provider: str,
        external_ids: list[str],
        workspace_id: str | None = None,
    ) -> int:
        if not external_ids:
            return 0
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                DELETE FROM documents
                WHERE org_id = %s::uuid
                  AND source_provider = %s
                  AND workspace_id IS NOT DISTINCT FROM %s::uuid
                  AND source_external_id = ANY(%s)
                RETURNING id
                """,
                (org_id, provider, workspace_id, list(external_ids)),
            ).fetchall()
        return len(rows)

    def delete_all_source_documents(
        self,
        org_id: str,
        provider: str,
        workspace_id: str | None = None,
    ) -> int:
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                DELETE FROM documents
                WHERE org_id = %s::uuid
                  AND source_provider = %s
                  AND workspace_id IS NOT DISTINCT FROM %s::uuid
                RETURNING id
                """,
                (org_id, provider, workspace_id),
            ).fetchall()
        return len(rows)

