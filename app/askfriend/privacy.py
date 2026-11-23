"""Does a conversation hold anything the recipient could not open themselves?

If so the asker must tick before the summary goes out: the summary repeats
earlier answers, and those answers repeat documents. Anything we cannot prove
the recipient may read counts as private (fail closed).
"""

from __future__ import annotations

import uuid

from ..db import get_connection
from ..memory.conversations import ConversationTurnRow
from ..security.visibility import viewer_clause
from ..sources.google_groups import viewer_for_person


def needs_approval(org_id: str, turns: list[ConversationTurnRow], recipient_email: str) -> bool:
    """True when the turns used an upload, a live read, or a cited document the
    recipient's own access does not open (a deleted one included).

    Ceiling: an answer that cites SOME documents is judged by those; its uncited
    passages are not checked (asking on every grounded answer would make the tick
    meaningless).
    """
    doc_ids: set[str] = set()
    for turn in turns:
        meta = turn.meta or {}
        # Uploads are the asker's own; live reads (GitHub) follow the asker's login.
        if meta.get("attachments") or meta.get("live_sources"):
            return True
        # Chart figures come from rows that are never cited.
        if turn.chart or meta.get("agent") == "insights":
            return True
        # Passages were shown but none was cited: we cannot tell whose they were.
        if (meta.get("citation_count") or 0) > 0 and not turn.cited:
            return True
        doc_ids.update(c["document_id"] for c in turn.cited if c.get("document_id"))
    if not doc_ids:
        return False
    try:
        for doc_id in doc_ids:
            uuid.UUID(doc_id)
    except ValueError:
        return True  # not a document we can check: private
    clause, params = viewer_clause(viewer_for_person(org_id, recipient_email), "d")
    with get_connection() as conn:
        (readable,) = conn.execute(
            "SELECT count(*) FROM documents d WHERE d.org_id = %s AND d.id = ANY(%s::uuid[])" + clause,
            (org_id, list(doc_ids), *params),
        ).fetchone()
    return readable < len(doc_ids)
