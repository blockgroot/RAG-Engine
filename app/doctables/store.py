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
            for position, table in enumerate(tables):
                row = conn.execute(
                    """
                    INSERT INTO doc_tables
                        (org_id, workspace_id, document_id, position, name,
                         columns, row_count, truncated, notes, origin)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (org_id, workspace_id, document_id, position, table.name,
                     Jsonb([c.as_dict() for c in table.columns]), len(table.cells),
                     table.truncated, list(table.notes), origin),
                ).fetchone()
                quotes = list(table.quotes) + [None] * (len(table.cells) - len(table.quotes))
                conn.cursor().executemany(
                    "INSERT INTO doc_table_rows (table_id, row_no, cells, raw, quote) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    [(row[0], i, Jsonb(cells), Jsonb(list(raw)), quote)
                     for i, (cells, raw, quote) in enumerate(
                         zip(table.cells, table.raw, quotes))],
                )
                written += len(table.cells)
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - re-raised as our own type
        raise ProviderError(
            f"doctables: could not store tables of document {document_id}", cause=exc
        ) from exc
    return written


def list_tables(
    *, org_id: str, workspace_id: str | None, viewer, limit: int = MAX_LISTED,
) -> list[TableRef]:
    """Every table this viewer may chart in this scope, newest document first."""
    access, params = _visible(viewer)
    sql = f"""
        SELECT t.id, t.name, t.columns, t.row_count, t.truncated, t.notes,
               d.title, d.source_provider, d.source_uri, t.origin
          FROM doc_tables t
          JOIN documents d ON d.id = t.document_id
         WHERE t.org_id = %(org_id)s
           {_scoped("t", workspace_id)}
           {access}
         ORDER BY d.source_last_modified DESC NULLS LAST, t.position
         LIMIT %(limit)s
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                sql, {"org_id": org_id, "workspace_id": workspace_id,
                      "limit": limit, **params},
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("doctables: could not list tables", cause=exc) from exc
    return [
        TableRef(
            id=str(r[0]), name=r[1], columns=tuple(r[2] or ()), row_count=r[3],
            truncated=bool(r[4]), notes=tuple(r[5] or ()), document_title=r[6],
            provider=r[7], source_uri=r[8], origin=r[9] or "table",
        )
        for r in rows
    ]


def get_table(table_id: str, *, org_id: str, workspace_id: str | None, viewer) -> TableRef | None:
    """One table, re-checked against the viewer at RUN time.

    The resolver offered it a moment ago, but a spec can arrive from anywhere
    (a stored dict, a later turn), so the access check is repeated here rather
    than trusted.
    """
    access, params = _visible(viewer)
    try:
        with get_connection() as conn:
            r = conn.execute(
                f"""
                SELECT t.id, t.name, t.columns, t.row_count, t.truncated, t.notes,
                       d.title, d.source_provider, d.source_uri, t.origin
                  FROM doc_tables t JOIN documents d ON d.id = t.document_id
                 WHERE t.id = %(table_id)s AND t.org_id = %(org_id)s
                   {_scoped("t", workspace_id)} {access}
                """,
                {"table_id": table_id, "org_id": org_id,
                 "workspace_id": workspace_id, **params},
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("doctables: could not read table", cause=exc) from exc
    if r is None:
        return None
    return TableRef(
        id=str(r[0]), name=r[1], columns=tuple(r[2] or ()), row_count=r[3],
        truncated=bool(r[4]), notes=tuple(r[5] or ()), document_title=r[6],
        provider=r[7], source_uri=r[8], origin=r[9] or "table",
    )


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
