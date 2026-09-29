"""Documents holding a flagged chunk, for the needs-attention bell.

Enforce mode leaves a flagged chunk out of every answer, which is silent by
design -- telling the ASKER invites probing. The document's owner is the one
person who can fix it (the text is in their Notion page or Drive file), so
they hear about it instead. Derived from `chunks`, never stored, like every
other bell item: it disappears the moment a re-sync scores the page clean.
"""

from __future__ import annotations

from ..db import get_connection

MAX_LISTED = 5


def flagged_documents(
    org_id: str, workspace_id: str | None, threshold: float
) -> list[tuple[str, str | None]]:
    """``(title, provider)`` of documents in exactly this scope with a flagged chunk."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT d.title, d.source_provider
            FROM documents d
            WHERE d.org_id = %s::uuid
              AND d.workspace_id IS NOT DISTINCT FROM %s::uuid
              AND EXISTS (
                SELECT 1 FROM chunks c
                WHERE c.document_id = d.id AND c.injection_score >= %s
              )
            ORDER BY d.title
            LIMIT %s
            """,
            (org_id, workspace_id, threshold, MAX_LISTED + 1),
        ).fetchall()
    return [(str(t or "Untitled"), p) for t, p in rows]
