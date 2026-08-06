"""Postgres + pgvector implementation of the ``VectorStore`` interface.

Every read and write is scoped by ``org_id`` — inserts stamp it on every row,
and ``query`` filters ``WHERE org_id = ...`` *before* ranking. That WHERE clause,
not any index, is what guarantees tenant isolation.
"""

from __future__ import annotations

import numpy as np

from ..config.settings import DatabaseSettings
from ..core.exceptions import EmbeddingProviderError, ProviderError
from ..db.connection import get_connection
from datetime import datetime

from .base import OrganizationRef, RetrievedChunk, StoredSourceDocument, VectorStore
from .bm25_ranking import bm25_rank


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
                    np.asarray(embedding, dtype=np.float32),
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
    ) -> list[RetrievedChunk]:
        if not query_embedding:
            raise EmbeddingProviderError("query_embedding is empty")

        vector = np.asarray(query_embedding, dtype=np.float32)
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
                ORDER BY c.embedding <=> %s
                LIMIT %s
                """,
                (vector, org_id, workspace_id, vector, top_k),
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
    ) -> list[RetrievedChunk]:
        """Full-text keyword search within ``org_id`` (Phase 6 hybrid retrieval).

        Phase 18: ranks matching chunks with in-process Okapi BM25 (see
        ``bm25_ranking.py``) instead of Postgres ``ts_rank``. Each row still
        carries cosine similarity vs ``query_embedding`` in ``score`` for the gate.
        """
        if not query_embedding:
            raise EmbeddingProviderError("query_embedding is empty")
        if not query_text.strip():
            return []

        vector = np.asarray(query_embedding, dtype=np.float32)
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                SELECT c.content,
                       c.document_id::text,
                       c.chunk_index,
                       c.org_id::text,
                       1 - (c.embedding <=> %s) AS score,
                       d.title
                FROM chunks c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.org_id = %s::uuid
                  AND c.workspace_id IS NOT DISTINCT FROM %s::uuid
                  AND c.content_tsv @@ websearch_to_tsquery('english', %s)
                """,
                (vector, org_id, workspace_id, query_text),
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
                    np.asarray(embedding, dtype=np.float32),
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

