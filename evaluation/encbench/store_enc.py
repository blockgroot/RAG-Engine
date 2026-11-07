"""The encrypted variant of the store: the app's two search legs over `chunks_enc`.

Same SQL as `PgVectorStore.query` and `keyword_search` (same filters, same
limits, same BM25 step), with three differences only:

- the text comes back encrypted and is decrypted here, after the query;
- the keyword leg finds candidates by word codes (`word_ints && codes`),
  ordered by how many of the question's codes a chunk has, where today it
  matches `content_tsv` and orders by `ts_rank`;
- the question's words are turned into codes first.

`crypto_ms` / `codes_ms` add up the time spent on encryption work, so a
sequential run can split a leg into database time and crypto time.
"""

from __future__ import annotations

import threading
import time

from app.vectorstore.base import RetrievedChunk
from app.vectorstore.bm25_ranking import bm25_rank
from app.vectorstore.pgvector_store import PgVectorStore, _to_db_vector, _viewer_clause
from app.db.connection import get_connection
from evaluation.encbench.crypto import KeyRing


class EncryptedStore(PgVectorStore):
    def __init__(self, keyring: KeyRing, settings=None) -> None:
        super().__init__(settings)
        self.keyring = keyring
        self.crypto_ms = 0.0
        self.codes_ms = 0.0
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self.crypto_ms = self.codes_ms = 0.0

    def _decrypt(self, org_id: str, blobs: list[bytes]) -> list[str]:
        t0 = time.perf_counter()
        keys = self.keyring.keys(org_id)
        out = [keys.decrypt(b) for b in blobs]
        with self._lock:
            self.crypto_ms += (time.perf_counter() - t0) * 1000
        return out

    def list_chunk_texts(self, org_id: str) -> list[str]:
        """The spelling normalizer's corpus: every chunk, decrypted (once per org per process)."""
        with get_connection(self._settings, org_id=org_id) as conn:
            rows = conn.execute("SELECT content FROM chunks_enc WHERE org_id = %s::uuid", (org_id,)).fetchall()
        return self._decrypt(org_id, [r[0] for r in rows])

    def query(self, org_id, query_embedding, top_k=5, workspace_id=None, source_provider=None,
              date_range=None, tags=None, viewer=None, document_ids=None):
        vector = _to_db_vector(query_embedding)
        after = date_range.after if date_range else None
        before = date_range.before if date_range else None
        viewer_sql, viewer_params = _viewer_clause(viewer)
        with get_connection(self._settings, org_id=org_id) as conn:
            rows = conn.execute(
                f"""
                SELECT c.content,
                       1 - (c.embedding <=> %s) AS score,
                       c.document_id::text, c.chunk_index, c.org_id::text,
                       d.title, d.source_provider, d.source_last_editor,
                       d.source_last_modified, coalesce(d.doc_is_public, TRUE),
                       c.injection_score, d.source_uri
                FROM chunks_enc c
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE c.org_id = %s::uuid
                  AND c.workspace_id IS NOT DISTINCT FROM %s::uuid
                  AND (%s::text IS NULL OR d.source_provider = %s::text)
                  AND (%s::timestamptz IS NULL OR d.source_last_modified >= %s::timestamptz)
                  AND (%s::timestamptz IS NULL OR d.source_last_modified <= %s::timestamptz)
                  AND (%s::text[] IS NULL OR d.tags && %s::text[])
                  AND (%s::uuid[] IS NULL OR c.document_id = ANY(%s::uuid[]))
                  {viewer_sql.lstrip()}
                ORDER BY c.embedding <=> %s
                LIMIT %s
                """,
                (vector, org_id, workspace_id, source_provider, source_provider, after, after,
                 before, before, tags, tags, document_ids, document_ids, *viewer_params,
                 vector, top_k),
            ).fetchall()
        texts = self._decrypt(org_id, [r[0] for r in rows])
        return [
            RetrievedChunk(
                content=t, score=float(r[1]), document_id=r[2], chunk_index=r[3], org_id=r[4],
                document_title=(str(r[5]).strip() if r[5] else None), source_provider=r[6],
                last_editor=(str(r[7]).strip() if r[7] else None), last_modified=r[8],
                doc_is_public=bool(r[9]), injection_score=r[10], source_uri=r[11],
            )
            for t, r in zip(texts, rows)
        ]

    def keyword_search(self, org_id, query_text, query_embedding, top_k=30, workspace_id=None,
                       source_provider=None, date_range=None, tags=None, viewer=None):
        if not query_text.strip():
            return []
        t0 = time.perf_counter()
        codes = self.keyring.keys(org_id).word_ints(query_text)
        with self._lock:
            self.codes_ms += (time.perf_counter() - t0) * 1000
        if not codes:
            return []
        vector = _to_db_vector(query_embedding)
        after = date_range.after if date_range else None
        before = date_range.before if date_range else None
        viewer_sql, viewer_params = _viewer_clause(viewer, alias="fd")
        with get_connection(self._settings, org_id=org_id) as conn:
            rows = conn.execute(
                f"""
                WITH matched AS (
                    SELECT c.content, c.document_id, c.chunk_index, c.org_id,
                           c.embedding, c.injection_score,
                           icount(c.word_ints & %s::int[]) AS hits
                    FROM chunks_enc c
                    LEFT JOIN documents fd ON fd.id = c.document_id
                    WHERE c.org_id = %s::uuid
                      AND c.workspace_id IS NOT DISTINCT FROM %s::uuid
                      AND c.word_ints && %s::int[]
                      AND (%s::text IS NULL OR fd.source_provider = %s::text)
                      AND (%s::timestamptz IS NULL OR fd.source_last_modified >= %s::timestamptz)
                      AND (%s::timestamptz IS NULL OR fd.source_last_modified <= %s::timestamptz)
                      AND (%s::text[] IS NULL OR fd.tags && %s::text[])
                      {viewer_sql.lstrip()}
                    ORDER BY hits DESC
                    LIMIT %s
                )
                SELECT m.content, m.document_id::text, m.chunk_index, m.org_id::text,
                       1 - (m.embedding <=> %s) AS score,
                       d.title, d.source_provider, d.source_last_editor, d.source_last_modified,
                       coalesce(d.doc_is_public, TRUE), m.injection_score, d.source_uri
                FROM matched m
                LEFT JOIN documents d ON d.id = m.document_id
                """,
                (codes, org_id, workspace_id, codes, source_provider, source_provider, after, after,
                 before, before, tags, tags, *viewer_params,
                 self._settings.keyword_candidate_limit, vector),
            ).fetchall()
        if not rows:
            return []
        contents = self._decrypt(org_id, [r[0] for r in rows])
        hits = bm25_rank(query_text, contents, top_k=min(top_k, len(contents)))
        return [
            RetrievedChunk(
                content=contents[i], score=float(rows[i][4]), document_id=rows[i][1],
                chunk_index=rows[i][2], org_id=rows[i][3],
                document_title=(str(rows[i][5]).strip() if rows[i][5] else None),
                source_provider=rows[i][6],
                last_editor=(str(rows[i][7]).strip() if rows[i][7] else None),
                last_modified=rows[i][8], doc_is_public=bool(rows[i][9]),
                injection_score=rows[i][10], source_uri=rows[i][11],
            )
            for i, _score in hits
        ]
