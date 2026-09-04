"""Postgres/pgvector implementation of the ``VectorStore`` interface."""

from __future__ import annotations

import numpy as np
from pgvector import Vector

from ..config.settings import DatabaseSettings
from ..core.exceptions import EmbeddingProviderError, ProviderError
from ..db.connection import get_connection
from datetime import datetime

from .base import DateRange, OrganizationRef, RetrievedChunk, StoredSourceDocument, VectorStore
from .bm25_ranking import bm25_rank

def _to_db_vector(embedding: list[float] | np.ndarray) -> Vector:
    """Bind an embedding as a pgvector ``Vector`` value."""
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
        tags: list[str] | None = None,
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
                INSERT INTO documents (org_id, title, source_uri, workspace_id, tags)
                VALUES (%s::uuid, %s, %s, %s::uuid, %s)
                RETURNING id
                """,
                (org_id, title, source_uri, workspace_id, tags),
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
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        if not query_embedding:
            raise EmbeddingProviderError("query_embedding is empty")

        vector = _to_db_vector(query_embedding)
        after = date_range.after if date_range else None
        before = date_range.before if date_range else None
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                SELECT c.content,
                       1 - (c.embedding <=> %s) AS score,
                       c.document_id::text,
                       c.chunk_index,
                       c.org_id::text,
                       d.title,
                       d.source_provider,
                       d.source_last_editor,
                       d.source_last_modified
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.org_id = %s::uuid
                  AND c.workspace_id IS NOT DISTINCT FROM %s::uuid
                  AND (%s::text IS NULL OR d.source_provider = %s::text)
                  AND (%s::timestamptz IS NULL OR d.source_last_modified >= %s::timestamptz)
                  AND (%s::timestamptz IS NULL OR d.source_last_modified <= %s::timestamptz)
                  AND (%s::text[] IS NULL OR d.tags && %s::text[])
                ORDER BY c.embedding <=> %s
                LIMIT %s
                """,
                (
                    vector,
                    org_id,
                    workspace_id,
                    source_provider,
                    source_provider,
                    after,
                    after,
                    before,
                    before,
                    tags,
                    tags,
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
                source_provider=row[6],
                last_editor=(str(row[7]).strip() if row[7] else None),
                last_modified=row[8],
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
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Full-text keyword search within one org."""
        if not query_embedding:
            raise EmbeddingProviderError("query_embedding is empty")
        if not query_text.strip():
            return []

        vector = _to_db_vector(query_embedding)
        after = date_range.after if date_range else None
        before = date_range.before if date_range else None
        params: list = [
            source_provider, source_provider, after, after, before, before, tags, tags,
        ]
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                WITH matched AS (
                    SELECT c.content,
                           c.document_id,
                           c.chunk_index,
                           c.org_id,
                           c.embedding
                    FROM chunks c
                    LEFT JOIN documents fd ON fd.id = c.document_id
                    WHERE c.org_id = %s::uuid
                      AND c.workspace_id IS NOT DISTINCT FROM %s::uuid
                      AND c.content_tsv @@ websearch_to_tsquery('english', %s)
                      AND (%s::text IS NULL OR fd.source_provider = %s::text)
                      AND (%s::timestamptz IS NULL OR fd.source_last_modified >= %s::timestamptz)
                      AND (%s::timestamptz IS NULL OR fd.source_last_modified <= %s::timestamptz)
                      AND (%s::text[] IS NULL OR fd.tags && %s::text[])
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
                       d.title,
                       d.source_provider,
                       d.source_last_editor,
                       d.source_last_modified
                FROM matched m
                LEFT JOIN documents d ON d.id = m.document_id
                """,
                (
                    org_id,
                    workspace_id,
                    query_text,
                    *params,
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
                    source_provider=row[6],
                    last_editor=(str(row[7]).strip() if row[7] else None),
                    last_modified=row[8],
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
        tags: list[str] | None = None,
        last_editor: str | None = None,
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
                    source_last_modified, workspace_id, tags, source_last_editor
                )
                VALUES (%s::uuid, %s, %s, %s, %s, %s, %s::uuid, %s, %s)
                RETURNING id
                """,
                (
                    org_id,
                    title,
                    source_uri,
                    provider,
                    external_id,
                    last_modified,
                    workspace_id,
                    tags,
                    last_editor,
                ),
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
        tags: list[str] | None = None,
        last_editor: str | None = None,
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
                    source_last_modified, workspace_id, tags, source_last_editor
                )
                VALUES (%s::uuid, %s, %s, %s, %s, %s, %s::uuid, %s, %s)
                RETURNING id
                """,
                (
                    org_id,
                    title,
                    source_uri,
                    provider,
                    external_id,
                    last_modified,
                    workspace_id,
                    tags,
                    last_editor,
                ),
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

