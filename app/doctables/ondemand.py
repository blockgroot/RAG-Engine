"""Read a relevant document's figures NOW, while the asker waits in Chart mode.

The background pass (``queue.py``) reads prose a few minutes after a sync, and
a document indexed before charts read tables waits for the per-sync backfill.
A question asked in that window used to get "I couldn't find that" about data
that was sitting in the source. So Chart mode, when nothing offered answers
the question, picks the few documents closest to it that have not been read
and reads them on the spot:

- the text comes from a LIVE fetch through the same source adapter ingestion
  uses (``fetch_document``: a Sheet's rows, a Doc's markdown, a PDF's text).
  Never from stored chunks: ingestion prepends model-written context to
  chunks, and a figure "verified" against model text would be invented.
- the table adapter (exact, no AI) and the text adapter (AI, every row
  checked against its sentence) run on it, and what they find is stored as
  that document's tables -- so the next question is answered from storage.

Bounded: at most ``limit`` documents, each read in its own thread, the whole
step capped at ``budget_s``. A read that misses the budget keeps running and
stores its result for the next question; this one moves on. Only documents the
asker may open in this scope are ever considered (the visibility predicate in
the same ``WHERE`` as ``org_id``).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass

from ..db.connection import get_connection
from .store import _visible

logger = logging.getLogger(__name__)

#: Providers whose documents can be re-fetched one at a time. Slack threads are
#: listings, not documents, and GitHub embeds nothing.
READABLE = ("notion", "google", "linear")

#: Chunks scanned per question before grouping by document. A bound, not a
#: tuning knob: the closest documents are among the closest chunks.
_CHUNK_SCAN = 200

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chart-read")


@dataclass(frozen=True)
class Unread:
    document_id: str
    provider: str
    external_id: str
    title: str
    score: float


def unread_documents(
    question: str, *, org_id: str, workspace_id: str | None, viewer,
    providers: set[str] | None, exclude_ids: set[str], floor: float, limit: int,
    title_query: str | None = None,
) -> list[Unread]:
    """Documents close to the question whose figures have not been read.

    "Not read" = no figures from text stored and no finished background pass,
    so a document the background already read and found nothing in is not
    read again. ``title_query`` adds the page the question named, whatever
    its score. No viewer, or the unrestricted one, means none.
    """
    if viewer is None or getattr(viewer, "is_unrestricted", False) or limit <= 0:
        return []
    wanted = [p for p in READABLE if providers is None or p in providers]
    if not wanted or not question:
        return []
    from ..agent.routing import _probe_embedder
    from .store import _title_like

    access, params = _visible(viewer)
    scope = ("c.workspace_id IS NULL" if workspace_id is None
             else "c.workspace_id = %(workspace_id)s")
    unread = """
        d.source_external_id IS NOT NULL
        AND d.source_provider = ANY(%(providers)s)
        AND NOT (d.id = ANY(%(exclude)s::uuid[]))
        AND NOT EXISTS (SELECT 1 FROM doc_text_queue q
                         WHERE q.document_id = d.id AND q.status IN ('done', 'failed'))
        AND NOT EXISTS (SELECT 1 FROM doc_tables t
                         WHERE t.document_id = d.id AND t.origin = 'text')
    """
    vector = _probe_embedder().embed([question])[0]
    args = {
        "org_id": org_id, "workspace_id": workspace_id, "vector": vector,
        "providers": wanted, "exclude": sorted(exclude_ids), "floor": floor,
        "limit": limit, "scan": _CHUNK_SCAN, **params,
    }
    sql = f"""
        WITH near AS (
            SELECT c.document_id, 1 - (c.embedding <=> %(vector)s::vector) AS score
              FROM chunks c
              JOIN documents d ON d.id = c.document_id AND d.org_id = c.org_id
             WHERE c.org_id = %(org_id)s AND {scope} {access}
             ORDER BY c.embedding <=> %(vector)s::vector
             LIMIT %(scan)s
        )
        SELECT d.id::text, d.source_provider, d.source_external_id, d.title,
               MAX(n.score) AS score
          FROM near n JOIN documents d ON d.id = n.document_id
         WHERE {unread}
         GROUP BY d.id, d.source_provider, d.source_external_id, d.title
        HAVING MAX(n.score) >= %(floor)s
         ORDER BY score DESC
         LIMIT %(limit)s
    """
    like = _title_like(title_query)
    named_sql = f"""
        SELECT d.id::text, d.source_provider, d.source_external_id, d.title, 1.0
          FROM documents d
         WHERE d.org_id = %(org_id)s
           AND {scope.replace("c.", "d.")} {access}
           AND d.title ILIKE %(like)s AND {unread}
         ORDER BY d.source_last_modified DESC NULLS LAST
         LIMIT 1
    """
    with get_connection() as conn:
        rows = []
        if like:
            rows += conn.execute(named_sql, {**args, "like": like}).fetchall()
        rows += conn.execute(sql, args).fetchall()
    out: list[Unread] = []
    seen: set[str] = set()
    for r in rows:
        if r[0] in seen:
            continue
        seen.add(r[0])
        out.append(Unread(r[0], r[1], r[2], r[3], float(r[4])))
    return out[:limit]


def read_now(doc: Unread, *, org_id: str, workspace_id: str | None) -> int:
    """Fetch one document live, store its tables and its figures from text.

    Returns how many tables were stored. Never raises: a document the source
    refuses (not found, no access, reconnect needed) costs this read only.
    """
    try:
        from ..auth import get_connection_config
        from ..auth.credentials import get_live_connection_token
        from ..config.settings import DocTablesSettings
        from ..llm.factory import build_llm_provider
        from ..sources import build_source_adapter
        from .base import DocumentText
        from .store import mark_checked, replace_document_tables
        from .table_adapter import TableAdapter
        from .text_adapter import TextAdapter

        token = get_live_connection_token(org_id, doc.provider, workspace_id=workspace_id)
        config = get_connection_config(org_id, doc.provider, workspace_id=workspace_id)
        source = build_source_adapter(doc.provider, token=token, config=config)
        fetched = source.fetch_document(doc.external_id)
        text = DocumentText(
            external_id=doc.external_id, title=fetched.title or doc.title,
            content=fetched.content or "",
            tables=tuple(fetched.tables) if getattr(fetched, "tables", None) is not None else None,
        )
        stored = 0
        exact = TableAdapter()
        if exact.wants(text):
            found = exact.extract(text)
            if found:
                replace_document_tables(doc.document_id, org_id=org_id,
                                        workspace_id=workspace_id, tables=found)
                stored += len(found)
        prose = TextAdapter(llm=build_llm_provider(),
                            max_chars=DocTablesSettings.from_env().text_max_chars)
        if prose.wants(text):
            found = prose.extract(text)
            # Stored even when empty: replacing with nothing clears figures
            # an older version of the page had.
            replace_document_tables(doc.document_id, org_id=org_id,
                                    workspace_id=workspace_id, tables=found,
                                    origin=prose.origin)
            stored += len(found)
            _mark_read(doc.document_id, org_id=org_id, workspace_id=workspace_id,
                       external_id=doc.external_id, title=text.title)
        mark_checked(doc.document_id)
        return stored
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("doctables: on-demand read of %s/%s failed",
                       doc.provider, doc.external_id, exc_info=True)
        return 0


def _mark_read(document_id: str, *, org_id: str, workspace_id: str | None,
               external_id: str, title: str) -> None:
    """Tell the background pass this document is done, so it is not read twice."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO doc_text_queue
                (document_id, org_id, workspace_id, origin, external_id, title, text, status)
            VALUES (%s, %s, %s, 'text', %s, %s, NULL, 'done')
            ON CONFLICT (document_id) DO UPDATE
               SET status = 'done', text = NULL, claimed_at = NULL, updated_at = now()
            """,
            (document_id, org_id, workspace_id, external_id, title[:300]),
        )
        conn.commit()


def read_relevant(docs: list[Unread], *, org_id: str, workspace_id: str | None,
                  budget_s: float) -> int:
    """Read ``docs`` in parallel within ``budget_s``. Tables stored in time."""
    if not docs:
        return 0
    futures = [_pool.submit(read_now, d, org_id=org_id, workspace_id=workspace_id)
               for d in docs]
    done, pending = wait(futures, timeout=budget_s)
    if pending:
        logger.info("doctables: %d on-demand read(s) still running past the budget",
                    len(pending))
    return sum(f.result() for f in done)
