"""Remembers that a page's sentences were read and held no series.

Figures in prose are read inline: at ingest (``ingestion.pipeline._store_tables``)
and, for a page ingest did not read, when someone asks for its chart. A page
whose sentences held nothing is marked ``done`` in ``doc_text_queue`` so the
next chart question does not pay for the same model call. The row cascades
with the document, so a re-ingested (edited) page is read again.
"""

from __future__ import annotations

from ..db.connection import get_connection


def mark_read(document_id: str, *, org_id: str, workspace_id: str | None,
              external_id: str, title: str) -> None:
    """Raises; the caller decides a missed mark only costs a repeat read."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO doc_text_queue
                (document_id, org_id, workspace_id, origin, external_id, title, status)
            VALUES (%s, %s, %s, 'text', %s, %s, 'done')
            ON CONFLICT (document_id) DO UPDATE
               SET status = 'done', text = NULL, claimed_at = NULL, updated_at = now()
            """,
            (document_id, org_id, workspace_id, external_id, (title or "")[:300]),
        )
        conn.commit()
