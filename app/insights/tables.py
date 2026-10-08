"""Charts of tables inside documents: offering them, and validating a pick.

The chart classifier (`resolve.py`) sees a handful of tables the ASKER may
open, as request-local handles (``T1``, ``T2``) -- the model never types a
database id, and an invented handle resolves to nothing (the
`livetools/handles.py` rule). It picks a table and names columns BY THEIR
HEADER TEXT; this module maps each name to the table's own column key and
refuses, naming the options, anything that does not fit: a sum of a text
column, a grouping by free text, a filter on a number.

Which tables are offered is decided by MEANING, not by shared words: the
question is compared with the stored embeddings of each table's document (a
Sheet's description of its columns, a page's text) -- the same index and
model retrieval uses, so it works in any wording or language and needs no
stop-word list. A table whose document does not clear the retrieval gate is
not offered: listing an unrelated sheet invites the model to chart it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..doctables.store import AGGREGATES, TableRef

#: How many tables a single question may see. Small because each one costs
#: prompt space on every chart-shaped question.
MAX_OFFERED = 6

MEASURES = ("count",) + tuple(AGGREGATES)

def document_similarity(
    question: str, tables: list[TableRef], *, org_id: str, workspace_id: str | None,
) -> dict[str, float] | None:
    """Best cosine between the question and each table's document, by
    document id. ``None`` when it cannot be measured (no embedder, no
    database): the caller then offers the newest tables instead."""
    ids = sorted({t.document_id for t in tables if t.document_id})
    if not question or not ids:
        return {}
    try:
        from ..agent.routing import _probe_embedder
        from ..db.connection import get_connection

        vector = _probe_embedder().embed([question])[0]
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT c.document_id::text, MAX(1 - (c.embedding <=> %s::vector))
                  FROM chunks c
                 WHERE c.org_id = %s
                   AND c.workspace_id IS NOT DISTINCT FROM %s
                   AND c.document_id = ANY(%s::uuid[])
                 GROUP BY c.document_id
                """,
                (vector, org_id, workspace_id, ids),
            ).fetchall()
    except Exception:  # noqa: BLE001 - ranking is a convenience, never a failure
        return None
    return {r[0]: float(r[1]) for r in rows if r[1] is not None}


def rank(
    tables: list[TableRef], scores: dict[str, float] | None, *,
    floor: float, limit: int = MAX_OFFERED,
) -> list[TableRef]:
    """Tables whose document is about the question, closest first.

    ``scores`` is ``document_similarity``; ``None`` (unmeasurable) keeps the
    given order -- newest document first -- capped at ``limit``.
    """
    if scores is None:
        return list(tables)[:limit]
    scored = [(scores.get(t.document_id or "", 0.0), i, t) for i, t in enumerate(tables)]
    kept = sorted((s for s in scored if s[0] >= floor), key=lambda s: (-s[0], s[1]))
    return [t for _, _, t in kept[:limit]]


def catalogue(tables: list[TableRef]) -> str:
    """The prompt section: one line per offered table, handle first."""
    lines = []
    for i, table in enumerate(tables, start=1):
        cols = []
        for c in table.columns:
            kind = c.get("type")
            if kind == "category":
                samples = ", ".join(str(s) for s in c.get("samples", [])[:3])
                cols.append(f"{c['name']} (category: {samples})")
            elif kind == "number":
                unit = f", {c['unit']}" if c.get("unit") else ""
                cols.append(f"{c['name']} (number{unit})")
            elif kind == "date":
                cols.append(f"{c['name']} (date)")
        kind = (" (figures read from the document's sentences)"
                if getattr(table, "origin", "table") == "text" else "")
        where = (f"in the file \"{table.document_title}\" the asker uploaded to this chat"
                 if getattr(table, "is_upload", False)
                 else f"in \"{table.document_title}\"{kind} [{table.provider}]")
        if getattr(table, "is_upload", False):
            where += kind.replace("document's", "file's")
        lines.append(
            f"- T{i}: \"{table.name}\" {where}, {table.row_count} rows. "
            f"Columns: {'; '.join(cols)}"
        )
    return "\n".join(lines)


class TableRefusal(ValueError):
    """The pick does not fit the table; the message names what would."""


@dataclass(frozen=True)
class TablePick:
    table_id: str
    measure: str
    value: str | None
    group_by: str | None
    split_by: str | None
    filters: tuple[tuple[str, str], ...]
    chart: str


def _find(table: TableRef, name, *, types: tuple[str, ...], role: str) -> str | None:
    """Header text -> column key, case- and punctuation-insensitive."""
    if name in (None, "", "null", "none"):
        return None
    if not isinstance(name, str):
        raise TableRefusal(f"I couldn't tell which column to use for the {role}.")
    squash = re.sub(r"[^a-z0-9]", "", name.lower())
    for c in table.columns:
        if re.sub(r"[^a-z0-9]", "", str(c.get("name", "")).lower()) == squash:
            if c.get("type") not in types:
                raise TableRefusal(
                    f"\"{c['name']}\" in {table.name} can't be the {role}: "
                    f"it holds {c.get('type')} values. "
                    + _options(table, types, role)
                )
            return c["key"]
    raise TableRefusal(f"{table.name} has no column called \"{name[:60]}\". "
                       + _options(table, types, role))


def _options(table: TableRef, types: tuple[str, ...], role: str) -> str:
    names = [c["name"] for c in table.columns if c.get("type") in types]
    return (f"Columns that work for the {role}: {', '.join(names)}."
            if names else f"This table has no column that works for the {role}.")


def parse_pick(data: dict, handles: dict[str, TableRef]) -> TablePick | None:
    """Validate the model's table pick. None = it did not pick a table.

    Raises ``TableRefusal`` for a pick that names a real table but does not
    fit it. A handle that was never offered is not a table at all, so it
    returns None and the ordinary metric path decides.
    """
    handle = data.get("table")
    if not isinstance(handle, str) or handle.strip().upper() not in handles:
        return None
    table = handles[handle.strip().upper()]

    group_by = _find(table, data.get("group_by"), types=("category", "date"),
                     role="breakdown")
    split_by = _find(table, data.get("split_by"), types=("category",),
                     role="second breakdown")
    if split_by and not group_by:
        group_by, split_by = split_by, None
    if split_by and split_by == group_by:
        split_by = None
    value = _find(table, data.get("value"), types=("number",), role="value to add up")

    measure = data.get("measure")
    measure = measure.lower() if isinstance(measure, str) else None
    if measure in (None, "", "null", "none", "total", "count_rows"):
        measure = "sum" if value else "count"
    if measure == "avg":
        measure = "average"
    if measure not in MEASURES:
        raise TableRefusal(f"I can count rows or take the {', '.join(AGGREGATES)} of "
                           "a number column.")
    if measure != "count" and not value:
        raise TableRefusal(f"To take the {measure} I need a number column. "
                           + _options(table, ("number",), "value to add up"))
    if measure == "count":
        value = None

    filters = []
    raw_filters = data.get("filters")
    if isinstance(raw_filters, dict):
        for name, wanted in raw_filters.items():
            if not isinstance(wanted, str) or not wanted.strip():
                continue
            key = _find(table, name, types=("category",), role="filter")
            if key:
                filters.append((key, wanted.strip()[:120]))

    group_type = table.column(group_by).get("type") if group_by else None
    requested = data.get("chart") if isinstance(data.get("chart"), str) else None
    if group_type == "date":
        chart = requested if requested in ("line", "bar") else "line"
    elif group_by:
        chart = requested if requested in ("bar", "pie") else "bar"
    else:
        chart = "bar"
    return TablePick(
        table_id=table.id, measure=measure, value=value, group_by=group_by,
        split_by=split_by, filters=tuple(sorted(filters)), chart=chart,
    )
