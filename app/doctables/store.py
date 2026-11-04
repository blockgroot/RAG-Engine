"""Document tables in Postgres: write at ingest, list for a viewer, aggregate.

Three rules carry over from ``insights/store.py`` unchanged, because a chart
of a spreadsheet is still a chart:

- every read is pinned to ``org_id`` and to ONE scope (org-wide = ``IS NULL``,
  a space = that space, never both);
- a viewer sees a table only if they may open its DOCUMENT
  (``visibility_predicate`` on the JOIN) -- a sheet shared with finance is not
  charted for sales;
- the only things spliced into SQL are OUR column keys (``c0``...), checked
  against a regex, a ``date_trunc`` unit from a closed set, and aggregate
  fragments from a closed map. Every value is bound.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from ..core.exceptions import ProviderError
from ..db.connection import get_connection
from ..security.visibility import visibility_predicate
from .extract import Table

logger = logging.getLogger(__name__)

#: How many visible tables are read before ranking against the question. A
#: bound, not a feature: a scope with thousands of tables is ranked on the
#: newest 200 and says so in the log.
MAX_LISTED = 200

PERIODS = ("day", "week", "month", "quarter")  # = insights.registry.PERIODS

#: Measure name -> aggregate over one number column (``{col}`` is a key).
AGGREGATES = {
    "sum": "sum(({col})::numeric)",
    "average": "avg(({col})::numeric)",
    "min": "min(({col})::numeric)",
    "max": "max(({col})::numeric)",
}

_KEY = re.compile(r"^c\d{1,2}$")

MAX_DETAILS = 12


@dataclass(frozen=True)
class TableRef:
    """One table a viewer may chart, as the resolver sees it."""

    id: str
    name: str
    columns: tuple[dict, ...]
    row_count: int
    truncated: bool
    notes: tuple[str, ...]
    document_title: str
    provider: str
    source_uri: str | None
    #: Which adapter wrote it: "table" (exact) or "text" (read from sentences).
    origin: str = "table"
    #: The document it sits in: what a question is compared with to decide
    #: which tables to offer (`insights.tables.document_similarity`).
    document_id: str | None = None
    #: Set instead of ``document_id`` for a table inside a file the asker
    #: uploaded to this chat: private to them, never in ``list_tables``.
    attachment_id: str | None = None

    @property
    def is_upload(self) -> bool:
        return self.attachment_id is not None

    def column(self, key: str) -> dict | None:
        return next((c for c in self.columns if c.get("key") == key), None)


def _scoped(alias: str, workspace_id: str | None) -> str:
    return (
        f" AND {alias}.workspace_id IS NULL" if workspace_id is None
        else f" AND {alias}.workspace_id = %(workspace_id)s"
    )


def _visible(viewer) -> tuple[str, dict]:
    """``("", {})`` for an unrestricted viewer (ingest, eval); otherwise the
    ONE document predicate, so a table can never be more visible than the
    document it came from."""
    if viewer is None or getattr(viewer, "is_unrestricted", False):
        return "", {}
    return f" AND {visibility_predicate('d', param='acl')}", {"acl": viewer.acl()}


def replace_document_tables(
    document_id: str, *, org_id: str, workspace_id: str | None, tables: list[Table],
    origin: str = "table",
) -> int:
    """Swap this document's tables for ``tables``. Returns rows written.

    Delete-then-insert in one transaction: a re-ingested sheet whose rows
    changed must not keep yesterday's rows beside today's. Raises
    ``ProviderError``; the ingest caller decides that a table failure costs
    the chart, never the indexed document.
    """
    written = 0
    try:
        with get_connection() as conn:
            # Only this adapter's tables: the text pass must never wipe the
            # tables ingestion stored, and a re-read of a sheet must never
            # wipe figures read from the same document's prose.
            conn.execute(
                "DELETE FROM doc_tables WHERE document_id = %s AND origin = %s",
                (document_id, origin),
            )
            written = _insert_tables(
                conn, tables, org_id=org_id, workspace_id=workspace_id,
                document_id=document_id, attachment_id=None, origin=origin,
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - re-raised as our own type
        raise ProviderError(
            f"doctables: could not store tables of document {document_id}", cause=exc
        ) from exc
    return written


def _insert_tables(
    conn, tables: list[Table], *, org_id: str, workspace_id: str | None,
    document_id: str | None, attachment_id: str | None, origin: str,
) -> int:
    written = 0
    for position, table in enumerate(tables):
        row = conn.execute(
            """
            INSERT INTO doc_tables
                (org_id, workspace_id, document_id, attachment_id, position, name,
                 columns, row_count, truncated, notes, origin)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (org_id, workspace_id, document_id, attachment_id, position, table.name,
             Jsonb([c.as_dict() for c in table.columns]), len(table.cells),
             table.truncated, list(table.notes), origin),
        ).fetchone()
        quotes = list(table.quotes) + [None] * (len(table.cells) - len(table.quotes))
        conn.cursor().executemany(
            "INSERT INTO doc_table_rows (table_id, row_no, cells, raw, quote) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(row[0], i, Jsonb(cells), Jsonb(list(raw)), quote)
             for i, (cells, raw, quote) in enumerate(zip(table.cells, table.raw, quotes))],
        )
        written += len(table.cells)
    return written


def replace_attachment_tables(
    attachment_id: str, *, org_id: str, user_id: str, tables: list[Table],
    origin: str = "table",
) -> int:
    """Swap one uploaded file's tables (this adapter's only) for ``tables``.

    The attachment row is re-read with its owner in the WHERE, so a caller
    can never hang tables off a file that is not that person's. Raises
    ``ProviderError``; the upload caller decides a table failure never
    fails the upload.
    """
    try:
        with get_connection() as conn:
            owner = conn.execute(
                "SELECT a.id FROM conversation_attachments a "
                "WHERE a.id = %s AND a.org_id = %s AND a.user_id = %s",
                (attachment_id, org_id, user_id),
            ).fetchone()
            if owner is None:
                return 0
            conn.execute(
                "DELETE FROM doc_tables WHERE attachment_id = %s AND origin = %s",
                (attachment_id, origin),
            )
            written = _insert_tables(
                conn, tables, org_id=org_id, workspace_id=None, document_id=None,
                attachment_id=attachment_id, origin=origin,
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - re-raised as our own type
        raise ProviderError(
            f"doctables: could not store tables of attachment {attachment_id}", cause=exc
        ) from exc
    return written


@dataclass(frozen=True)
class UploadScope:
    """Whose uploads may be charted: this person's files in this chat.

    The only key to an upload's tables. Built in ``api/chat.py`` from the
    session (``user_id``) and the conversation the route already checked.
    """

    conversation_id: str
    user_id: str


_UPLOAD_COLUMNS = """
    SELECT t.id, t.name, t.columns, t.row_count, t.truncated, t.notes,
           a.filename, 'attachment', NULL, t.origin, NULL, t.attachment_id
      FROM doc_tables t
      JOIN conversation_attachments a ON a.id = t.attachment_id
     WHERE t.org_id = %(org_id)s AND a.org_id = %(org_id)s
       AND a.conversation_id = %(conversation_id)s AND a.user_id = %(user_id)s
"""


def _ref(r) -> TableRef:
    return TableRef(
        id=str(r[0]), name=r[1], columns=tuple(r[2] or ()), row_count=r[3],
        truncated=bool(r[4]), notes=tuple(r[5] or ()), document_title=r[6],
        provider=r[7], source_uri=r[8], origin=r[9] or "table",
        document_id=str(r[10]) if r[10] else None,
        attachment_id=str(r[11]) if len(r) > 11 and r[11] else None,
    )


def list_upload_tables(*, org_id: str, uploads: UploadScope | None) -> list[TableRef]:
    """Tables in the files this person uploaded to this chat, newest first."""
    if uploads is None:
        return []
    try:
        with get_connection() as conn:
            rows = conn.execute(
                _UPLOAD_COLUMNS + " ORDER BY a.created_at DESC, t.origin, t.position LIMIT %(limit)s",
                {"org_id": org_id, "conversation_id": uploads.conversation_id,
                 "user_id": uploads.user_id, "limit": MAX_LISTED},
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("doctables: could not list upload tables", cause=exc) from exc
    return [_ref(r) for r in rows]


@dataclass(frozen=True)
class DocumentPlace:
    """A document title this person may know about, and where it lives.

    ``workspace_name`` is None when the document is in the scope being asked.
    """

    title: str
    workspace_name: str | None
    has_table: bool


#: Newest titles only. A chart gap is about a page the question names, not
#: a scan of every document the org has ever synced.
_TITLE_CAP = 400


def _title_like(fragment: str | None) -> str | None:
    """A bound ILIKE pattern for a page title the asker named. None when it
    is too short to be a title. ``%`` and ``_`` in the name are not wildcards."""
    text = re.sub(r"[%_\\]", "", (fragment or "").strip())
    if len(re.sub(r"\W", "", text)) < 8:
        return None
    return f"%{text}%"


def document_places(
    *, org_id: str, workspace_id: str | None, viewer, user_id: str | None,
    title_query: str | None = None,
) -> list[DocumentPlace]:
    """Titles this viewer may see, in this scope and in workspaces they belong to.

    Used when a chart names a page that was not offered. A page in a workspace
    they are not a member of is not returned, so Company-wide never learns
    that a private workspace holds it. No viewer means none.
    """
    if viewer is None or getattr(viewer, "is_unrestricted", False):
        return []
    access, params = _visible(viewer)
    has_table = (
        "EXISTS (SELECT 1 FROM doc_tables t "
        "WHERE t.document_id = d.id AND t.org_id = d.org_id)"
    )
    scope = (
        "d.workspace_id IS NULL" if workspace_id is None
        else "d.workspace_id = %(workspace_id)s"
    )
    sql = f"""
        SELECT d.title, NULL::text, {has_table}
          FROM documents d
         WHERE d.org_id = %(org_id)s AND {scope}
           AND d.title IS NOT NULL
           {access}
         ORDER BY d.source_last_modified DESC NULLS LAST
         LIMIT %(limit)s
    """
    other = f"""
        SELECT d.title, w.name, {has_table}
          FROM documents d
          JOIN workspaces w ON w.id = d.workspace_id AND w.org_id = d.org_id
          JOIN workspace_members wm
            ON wm.workspace_id = w.id AND wm.user_id = %(user_id)s::uuid
         WHERE d.org_id = %(org_id)s
           AND d.workspace_id IS DISTINCT FROM %(workspace_id)s::uuid
           AND d.title IS NOT NULL
           {access}
         ORDER BY d.source_last_modified DESC NULLS LAST
         LIMIT %(limit)s
    """ if user_id else ""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                sql, {"org_id": org_id, "workspace_id": workspace_id,
                      "limit": _TITLE_CAP, **params},
            ).fetchall()
            if other:
                rows += conn.execute(
                    other,
                    {"org_id": org_id, "workspace_id": workspace_id,
                     "user_id": user_id, "limit": _TITLE_CAP, **params},
                ).fetchall()
            # The newest-400 list misses an older page. The title they named
            # is looked up on its own, still only in scopes they can see.
            like = _title_like(title_query)
            if like:
                rows += conn.execute(
                    f"""
                    SELECT d.title, NULL::text, {has_table}
                      FROM documents d
                     WHERE d.org_id = %(org_id)s AND {scope}
                       AND d.title ILIKE %(like)s
                       {access}
                     LIMIT 20
                    """,
                    {"org_id": org_id, "workspace_id": workspace_id, "like": like, **params},
                ).fetchall()
                if user_id:
                    rows += conn.execute(
                        f"""
                        SELECT d.title, w.name, {has_table}
                          FROM documents d
                          JOIN workspaces w ON w.id = d.workspace_id AND w.org_id = d.org_id
                          JOIN workspace_members wm
                            ON wm.workspace_id = w.id AND wm.user_id = %(user_id)s::uuid
                         WHERE d.org_id = %(org_id)s
                           AND d.workspace_id IS DISTINCT FROM %(workspace_id)s::uuid
                           AND d.title ILIKE %(like)s
                           {access}
                         LIMIT 20
                        """,
                        {"org_id": org_id, "workspace_id": workspace_id,
                         "user_id": user_id, "like": like, **params},
                    ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("doctables: could not look up document titles", cause=exc) from exc
    return [DocumentPlace(title=r[0], workspace_name=r[1], has_table=bool(r[2])) for r in rows]


def find_chart_document(
    *, org_id: str, workspace_id: str | None, viewer, title_query: str | None,
    providers: list[str] | None = None,
) -> tuple[str, str, str, str] | None:
    """One Notion, Drive, or Linear document in this scope whose title matches, or None.

    ``(id, provider, external_id, title)``. The live chart read uses this so
    it can only re-read a page the asker may already open here. ``providers``
    keeps it on the connector the question named.
    """
    like = _title_like(title_query)
    if not like or viewer is None or getattr(viewer, "is_unrestricted", False):
        return None
    access, params = _visible(viewer)
    scope = (
        "d.workspace_id IS NULL" if workspace_id is None
        else "d.workspace_id = %(workspace_id)s"
    )
    try:
        with get_connection() as conn:
            row = conn.execute(
                f"""
                SELECT d.id::text, d.source_provider, d.source_external_id, d.title
                  FROM documents d
                 WHERE d.org_id = %(org_id)s AND {scope}
                   AND d.source_provider IN ('notion', 'google', 'linear')
                   AND (%(providers)s::text[] IS NULL OR d.source_provider = ANY(%(providers)s))
                   AND d.source_external_id IS NOT NULL
                   AND d.title ILIKE %(like)s
                   {access}
                 ORDER BY d.source_last_modified DESC NULLS LAST
                 LIMIT 1
                """,
                {"org_id": org_id, "workspace_id": workspace_id, "like": like,
                 "providers": providers, **params},
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("doctables: could not find the named page", cause=exc) from exc
    if row is None or not row[2]:
        return None
    return (row[0], row[1], row[2], row[3])


#: Chunks of one page loaded when Chart mode reads its sentences. The text
#: adapter cuts to its own character cap; this only stops a huge page loading
#: whole. ponytail: a figure that starts past chunk 8 is unread until the page
#: is shorter or this cap is raised.
MAX_PROSE_CHUNKS = 8


def matching_prose(
    *, org_id: str, workspace_id: str | None, viewer, question: str,
    providers: list[str] | None = None, limit: int = 6, tag: str | None = None,
) -> list[tuple[str, str, str, str, bool]]:
    """Pages whose indexed text matches the question and have no sentence-figures yet.

    ``(document_id, external_id, title, text, truncated)``. The viewer predicate
    is on the document, in the same WHERE as ``org_id``. An unrestricted viewer
    gets nothing: this is a person's chart question, not ingest. ``tag`` keeps
    only documents carrying it (one Slack channel).
    """
    question = (question or "").strip()[:500]
    if not question or viewer is None or getattr(viewer, "is_unrestricted", False):
        return []
    access, params = _visible(viewer)
    if not access:
        return []
    scope = _scoped("d", workspace_id)
    query = (
        "to_tsquery('english', replace(plainto_tsquery('english', %(question)s)::text, '&', '|'))"
    )
    sql = f"""
        WITH ranked AS (
            SELECT d.id, d.workspace_id, d.source_external_id, d.title,
                   max(ts_rank(c.content_tsv, {query})) AS rank
              FROM documents d
              JOIN chunks c
                ON c.document_id = d.id
               AND c.org_id = d.org_id
               AND c.workspace_id IS NOT DISTINCT FROM d.workspace_id
             WHERE d.org_id = %(org_id)s
               {scope}
               AND c.content_tsv @@ {query}
               AND (%(providers)s::text[] IS NULL OR d.source_provider = ANY(%(providers)s))
               AND (%(tag)s::text IS NULL OR d.tags @> ARRAY[%(tag)s::text])
               AND NOT EXISTS (
                    SELECT 1 FROM doc_tables t
                     WHERE t.document_id = d.id AND t.org_id = d.org_id
                       AND t.origin = 'text'
               )
               AND NOT EXISTS (
                    SELECT 1 FROM doc_text_queue q
                     WHERE q.document_id = d.id AND q.org_id = d.org_id
                       AND q.status = 'done'
               )
               {access}
             GROUP BY d.id, d.workspace_id, d.source_external_id, d.title
             ORDER BY rank DESC
             LIMIT %(limit)s
        )
        SELECT r.id::text, coalesce(r.source_external_id, r.id::text), r.title,
               c.chunk_index, c.content,
               (SELECT count(*) FROM chunks c2
                 WHERE c2.document_id = r.id AND c2.org_id = %(org_id)s
                   AND c2.workspace_id IS NOT DISTINCT FROM r.workspace_id) AS nchunks
          FROM ranked r
          JOIN chunks c
            ON c.document_id = r.id
           AND c.org_id = %(org_id)s
           AND c.workspace_id IS NOT DISTINCT FROM r.workspace_id
           AND c.chunk_index < %(max_chunks)s
         ORDER BY r.rank DESC, r.id, c.chunk_index
    """
    try:
        with get_connection(org_id=org_id) as conn:
            rows = conn.execute(
                sql,
                {"org_id": org_id, "workspace_id": workspace_id, "question": question,
                 "providers": providers, "limit": limit, "max_chunks": MAX_PROSE_CHUNKS,
                 "tag": tag, **params},
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("doctables: could not find pages with figures", cause=exc) from exc
    order: list[str] = []
    pages: dict[str, list] = {}
    counts: dict[str, int] = {}
    for document_id, external_id, title, _index, content, nchunks in rows:
        if document_id not in pages:
            order.append(document_id)
            pages[document_id] = [external_id or document_id, title or "", []]
            counts[document_id] = int(nchunks or 0)
        pages[document_id][2].append(content or "")
    out = []
    for document_id in order:
        external_id, title, chunks = pages[document_id]
        out.append((
            document_id, external_id, title, "\n".join(chunks),
            counts[document_id] > MAX_PROSE_CHUNKS,
        ))
    return out


def list_tables(
    *, org_id: str, workspace_id: str | None, viewer, limit: int = MAX_LISTED,
    title_query: str | None = None, providers: list[str] | None = None,
    tag: str | None = None,
) -> list[TableRef]:
    """Every table this viewer may chart in this scope, newest document first.

    ``title_query`` keeps only documents whose title contains that name, so a
    page they named is found even when newer tables filled the usual list.
    ``providers`` keeps one connector's documents (a question that names
    Drive is never charted from Notion); ``tag`` one Slack channel's.
    """
    access, params = _visible(viewer)
    like = _title_like(title_query)
    titled = " AND d.title ILIKE %(like)s" if like else ""
    if providers:
        titled += " AND d.source_provider = ANY(%(providers)s)"
    if tag:
        titled += " AND d.tags @> ARRAY[%(tag)s::text]"
    sql = f"""
        SELECT t.id, t.name, t.columns, t.row_count, t.truncated, t.notes,
               d.title, d.source_provider, d.source_uri, t.origin, t.document_id
          FROM doc_tables t
          JOIN documents d ON d.id = t.document_id
         WHERE t.org_id = %(org_id)s
           {_scoped("t", workspace_id)}
           {titled}
           {access}
         ORDER BY d.source_last_modified DESC NULLS LAST, t.position
         LIMIT %(limit)s
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                sql, {"org_id": org_id, "workspace_id": workspace_id,
                      "limit": limit, "like": like, "providers": providers,
                      "tag": tag, **params},
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("doctables: could not list tables", cause=exc) from exc
    return [_ref(r) for r in rows]


def get_table(
    table_id: str, *, org_id: str, workspace_id: str | None, viewer,
    uploads: UploadScope | None = None,
) -> TableRef | None:
    """One table, re-checked against the viewer at RUN time.

    The resolver offered it a moment ago, but a spec can arrive from anywhere
    (a stored dict, a later turn), so the access check is repeated here rather
    than trusted. A document's table needs the document predicate; an
    upload's table needs its uploader and chat (``uploads``), and without
    them is simply not found.
    """
    access, params = _visible(viewer)
    try:
        with get_connection() as conn:
            r = conn.execute(
                f"""
                SELECT t.id, t.name, t.columns, t.row_count, t.truncated, t.notes,
                       d.title, d.source_provider, d.source_uri, t.origin, t.document_id
                  FROM doc_tables t JOIN documents d ON d.id = t.document_id
                 WHERE t.id = %(table_id)s AND t.org_id = %(org_id)s
                   {_scoped("t", workspace_id)} {access}
                """,
                {"table_id": table_id, "org_id": org_id,
                 "workspace_id": workspace_id, **params},
            ).fetchone()
            if r is None and uploads is not None:
                r = conn.execute(
                    _UPLOAD_COLUMNS + " AND t.id = %(table_id)s",
                    {"table_id": table_id, "org_id": org_id,
                     "conversation_id": uploads.conversation_id,
                     "user_id": uploads.user_id},
                ).fetchone()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("doctables: could not read table", cause=exc) from exc
    return _ref(r) if r is not None else None


def _col(key: str) -> str:
    if not _KEY.match(key or ""):
        raise ValueError(f"not a column key: {key!r}")
    return f"(r.cells->>'{key}')"


@dataclass(frozen=True)
class TablePoint:
    bucket: str | None
    group: str | None
    series: str | None
    value: float


def run_table_query(
    table: TableRef,
    *,
    measure: str,
    value_key: str | None,
    group_key: str | None,
    split_key: str | None,
    period: str,
    filters: tuple[tuple[str, str], ...] = (),
) -> list[TablePoint]:
    """Aggregate one table's rows. The table was already access-checked.

    A DATE grouping becomes the time axis (``date_trunc(period, ...)``) and
    the split, if any, becomes the series; a category grouping is the
    category axis. A plain count counts rows; every other measure aggregates
    one NUMBER column, and a cell that did not parse is simply absent, so it
    is left out of the sum rather than counted as zero.
    """
    if period not in PERIODS:
        raise ValueError(f"unknown period {period!r}")
    if measure == "count":
        select = "count(*)"
    elif measure in AGGREGATES:
        if not value_key:
            raise ValueError(f"{measure} needs a number column")
        select = AGGREGATES[measure].format(col=_col(value_key))
    else:
        raise ValueError(f"unknown measure {measure!r}")

    group_col = table.column(group_key) if group_key else None
    if group_key and group_col is None:
        raise ValueError(f"no column {group_key!r}")
    if split_key and table.column(split_key) is None:
        raise ValueError(f"no column {split_key!r}")

    if group_col is not None and group_col.get("type") == "date":
        bucket = f"date_trunc('{period}', ({_col(group_key)})::date)"
        group = _col(split_key) if split_key else "NULL"
        series = "NULL"
    else:
        bucket = "NULL::timestamp"
        group = _col(group_key) if group_key else "NULL"
        series = _col(split_key) if split_key else "NULL"

    where, params = "", {"table_id": table.id}
    for dim, value in filters:
        where += f" AND {_col(dim)} = %(f_{dim})s"
        params[f"f_{dim}"] = value
    if value_key and measure != "count":
        # Rows with no parseable number are not zero; they are absent.
        where += f" AND {_col(value_key)} IS NOT NULL"

    sql = f"""
        SELECT {bucket} AS bucket, {group}::text AS "group", {series}::text AS series,
               {select} AS value
          FROM doc_table_rows r
         WHERE r.table_id = %(table_id)s {where}
         GROUP BY 1, 2, 3
         ORDER BY 1 NULLS FIRST, min(r.row_no)
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"doctables: query on {table.id} failed", cause=exc) from exc
    return [
        TablePoint(
            bucket=r[0].isoformat() if r[0] is not None else None,
            group=r[1], series=r[2], value=float(r[3] or 0),
        )
        for r in rows
    ]


def count_rows(
    table: TableRef, *, filters: tuple[tuple[str, str], ...] = (), value_key: str | None = None,
) -> int:
    """How many rows a chart of this table was built from: the filters
    applied, and (for a sum or average) only rows whose value parsed."""
    where, params = "", {"table_id": table.id}
    for dim, value in filters:
        where += f" AND {_col(dim)} = %(f_{dim})s"
        params[f"f_{dim}"] = value
    if value_key:
        where += f" AND {_col(value_key)} IS NOT NULL"
    try:
        with get_connection() as conn:
            row = conn.execute(
                f"SELECT count(*) FROM doc_table_rows r WHERE r.table_id = %(table_id)s {where}",
                params,
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("doctables: could not count rows", cause=exc) from exc
    return int(row[0])


def list_values(table: TableRef, key: str) -> list[str]:
    """Distinct values of one column, for resolving a typed filter."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT {_col(key)} FROM doc_table_rows r "
                f"WHERE r.table_id = %s AND {_col(key)} IS NOT NULL ORDER BY 1",
                (table.id,),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("doctables: could not list values", cause=exc) from exc
    return [r[0] for r in rows]


def list_rows(
    table: TableRef, *, filters: tuple[tuple[str, str], ...] = (), limit: int = MAX_DETAILS,
) -> list[tuple[dict, list[str], str | None]]:
    """The first rows behind a chart, ``(cells, raw, quote)``, in document
    order. ``quote`` is the sentence a text-adapter row was read from."""
    where, params = "", {"table_id": table.id, "limit": max(1, min(limit, MAX_DETAILS))}
    for dim, value in filters:
        where += f" AND {_col(dim)} = %(f_{dim})s"
        params[f"f_{dim}"] = value
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT r.cells, r.raw, r.quote FROM doc_table_rows r "
                f"WHERE r.table_id = %(table_id)s {where} ORDER BY r.row_no LIMIT %(limit)s",
                params,
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("doctables: could not list rows", cause=exc) from exc
    return [(r[0] or {}, list(r[1] or []), r[2]) for r in rows]


def mark_checked(document_id: str) -> None:
    """Record that the dataset adapters have looked at this document."""
    with get_connection() as conn:
        conn.execute("UPDATE documents SET tables_checked_at = now() WHERE id = %s",
                     (document_id,))
        conn.commit()


def list_unchecked(
    *, org_id: str, provider: str, workspace_id: str | None, limit: int,
) -> list[tuple[str, str]]:
    """``(document_id, external_id)`` of indexed documents the adapters have
    never looked at, newest first: the documents questions are about."""
    if limit <= 0:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, source_external_id FROM documents
            WHERE org_id = %s::uuid
              AND source_provider = %s
              AND workspace_id IS NOT DISTINCT FROM %s::uuid
              AND source_external_id IS NOT NULL
              AND tables_checked_at IS NULL
            ORDER BY source_last_modified DESC NULLS LAST
            LIMIT %s
            """,
            (org_id, provider, workspace_id, limit),
        ).fetchall()
    return [(str(r[0]), r[1]) for r in rows]


@dataclass(frozen=True)
class FigureHealth:
    """How reading figures from this org's documents is going, from what is
    stored -- so nobody has to read logs to know a page was missed."""

    pages_with_figures: int
    tables: int
    no_figures: int
    retrying: int
    gave_up: int
    waiting: int
    #: ``(title, provider, attempts, last_try)`` of pages that failed to read,
    #: worst first; only pages the viewer may open are named.
    problems: tuple[tuple[str, str, int, str], ...]


def figure_health(*, org_id: str, viewer, max_tries: int, limit: int = 20) -> FigureHealth:
    """Counts for the whole org (numbers name nothing); titles through the
    document access predicate. Raises; the caller shows the rest of its page."""
    from ..security.visibility import viewer_clause

    clause, params = viewer_clause(viewer, "d")
    with get_connection() as conn:
        counts = conn.execute(
            """
            SELECT
              (SELECT count(DISTINCT t.document_id) FROM doc_tables t
                WHERE t.org_id = %(o)s AND t.origin = 'text' AND t.document_id IS NOT NULL),
              (SELECT count(*) FROM doc_tables t
                WHERE t.org_id = %(o)s AND t.origin = 'text' AND t.document_id IS NOT NULL),
              (SELECT count(*) FROM doc_text_queue q WHERE q.org_id = %(o)s AND q.status = 'done'),
              (SELECT count(*) FROM doc_text_queue q JOIN documents d ON d.id = q.document_id
                WHERE q.org_id = %(o)s AND q.status = 'unreadable' AND d.tables_checked_at IS NULL),
              (SELECT count(*) FROM doc_text_queue q JOIN documents d ON d.id = q.document_id
                WHERE q.org_id = %(o)s AND q.status = 'unreadable' AND d.tables_checked_at IS NOT NULL),
              (SELECT count(*) FROM documents d
                WHERE d.org_id = %(o)s AND d.tables_checked_at IS NULL
                  AND d.source_external_id IS NOT NULL)
            """,
            {"o": org_id},
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT d.title, d.source_provider, q.attempts, q.updated_at::text
              FROM doc_text_queue q JOIN documents d ON d.id = q.document_id
             WHERE q.org_id = %s AND q.status = 'unreadable'{clause}
             ORDER BY q.attempts DESC, q.updated_at DESC
             LIMIT %s
            """,
            [org_id, *params, limit],
        ).fetchall()
    return FigureHealth(
        pages_with_figures=int(counts[0]), tables=int(counts[1]), no_figures=int(counts[2]),
        retrying=int(counts[3]), gave_up=int(counts[4]), waiting=int(counts[5]),
        problems=tuple((r[0] or "Untitled", r[1] or "", min(int(r[2]), max_tries), r[3])
                       for r in rows),
    )
