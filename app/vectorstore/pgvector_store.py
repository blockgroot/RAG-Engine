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
from .base import RetrievedChunk, VectorStore


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

    def add_document(
        self,
        org_id: str,
        title: str,
        chunks: list[str],
        embeddings: list[list[float]],
        source_uri: str | None = None,
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
                INSERT INTO documents (org_id, title, source_uri)
                VALUES (%s::uuid, %s, %s)
                RETURNING id
                """,
                (org_id, title, source_uri),
            ).fetchone()
            document_id = doc_row[0]

            rows = [
                (org_id, document_id, index, content, np.asarray(embedding, dtype=np.float32))
                for index, (content, embedding) in enumerate(zip(chunks, embeddings))
            ]
            conn.cursor().executemany(
                """
                INSERT INTO chunks (org_id, document_id, chunk_index, content, embedding)
                VALUES (%s::uuid, %s, %s, %s, %s)
                """,
                rows,
            )

        return str(document_id)

    def query(
        self,
        org_id: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        if not query_embedding:
            raise EmbeddingProviderError("query_embedding is empty")

        vector = np.asarray(query_embedding, dtype=np.float32)
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                SELECT content,
                       1 - (embedding <=> %s) AS score,
                       document_id::text,
                       chunk_index,
                       org_id::text
                FROM chunks
                WHERE org_id = %s::uuid
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (vector, org_id, vector, top_k),
            ).fetchall()

        return [
            RetrievedChunk(
                content=row[0],
                score=float(row[1]),
                document_id=row[2],
                chunk_index=row[3],
                org_id=row[4],
            )
            for row in rows
        ]

    def keyword_search(
        self,
        org_id: str,
        query_text: str,
        query_embedding: list[float],
        top_k: int = 30,
    ) -> list[RetrievedChunk]:
        """Full-text keyword search within ``org_id`` (Phase 6 hybrid retrieval).

        Ordered by ``ts_rank`` (keyword relevance), but each row still carries its
        cosine similarity vs ``query_embedding`` in ``score`` — so a keyword hit
        flows through the same cosine-based confidence gate as a vector hit, and
        RRF fusion can use the keyword *rank order* independently.
        """
        if not query_embedding:
            raise EmbeddingProviderError("query_embedding is empty")
        if not query_text.strip():
            return []

        vector = np.asarray(query_embedding, dtype=np.float32)
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                SELECT content,
                       1 - (embedding <=> %s) AS score,
                       document_id::text,
                       chunk_index,
                       org_id::text
                FROM chunks
                WHERE org_id = %s::uuid
                  AND content_tsv @@ websearch_to_tsquery('english', %s)
                ORDER BY ts_rank(content_tsv, websearch_to_tsquery('english', %s)) DESC
                LIMIT %s
                """,
                (vector, org_id, query_text, query_text, top_k),
            ).fetchall()

        return [
            RetrievedChunk(
                content=row[0],
                score=float(row[1]),
                document_id=row[2],
                chunk_index=row[3],
                org_id=row[4],
            )
            for row in rows
        ]
