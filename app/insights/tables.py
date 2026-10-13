"""Charts of tables inside documents: offering them, and validating a pick.

The chart classifier (`resolve.py`) sees a handful of tables the ASKER may
open, as request-local handles (``T1``, ``T2``) -- the model never types a
database id, and an invented handle resolves to nothing (the
`livetools/handles.py` rule). It picks a table and names columns BY THEIR
HEADER TEXT; this module maps each name to the table's own column key and
refuses, naming the options, anything that does not fit: a sum of a text
column, a grouping by free text, a filter on a number.

Which tables are offered is the same merge Ask uses: the question against
each table's document embedding, and the any-word keyword match on that
document's text and title. Reciprocal rank fusion orders them. A table is
never dropped for a low score: a page that is mostly numbers embeds poorly,
and the words the asker used are what still find it.
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
) -> tuple[dict[str, float], dict[str, float]] | None:
    """Cosine and keyword score of the question against each table's document.

    ``(cosine, keywords)`` by document id. The keyword score is the any-word
    match Ask uses, on the chunk text or the document title, whichever is
    stronger. ``None`` when it cannot be measured (no embedder, no database):
    the caller then offers the newest tables instead.
    """
    ids = sorted({t.document_id for t in tables if t.document_id})
    if not question or not ids:
        return {}, {}
    try:
        from ..agent.routing import _probe_embedder
        from ..db.connection import get_connection
        from ..vectorstore.pgvector_store import _ANY_WORD

        vector = _probe_embedder().embed([question])[0]
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT c.document_id::text,
                       MAX(1 - (c.embedding <=> %s::vector)),
                       GREATEST(
                         COALESCE(MAX(ts_rank(c.content_tsv, {_ANY_WORD})), 0),
                         ts_rank(
                           to_tsvector('english', coalesce(MAX(d.title), '')),
                           {_ANY_WORD}))
                  FROM chunks c
                  JOIN documents d ON d.id = c.document_id AND d.org_id = c.org_id
                 WHERE c.org_id = %s
                   AND c.workspace_id IS NOT DISTINCT FROM %s
                   AND c.document_id = ANY(%s::uuid[])
                 GROUP BY c.document_id
                """,
                (vector, question, question, org_id, workspace_id, ids),
            ).fetchall()
    except Exception:  # noqa: BLE001 - ranking is a convenience, never a failure
        return None
    cosine = {r[0]: float(r[1]) for r in rows if r[1] is not None}
    keywords = {r[0]: float(r[2]) for r in rows if r[2]}
    return cosine, keywords


def rank(
    tables: list[TableRef], scores: dict[str, float] | None, *,
    floor: float, limit: int = MAX_OFFERED,
    keywords: dict[str, float] | None = None,
) -> list[TableRef]:
    """Tables closest to the question first, capped at ``limit``.

    ``scores`` is the cosine half of ``document_similarity``. Below ``floor``
    is order, never removal. ``keywords`` is the any-word half; the two lists
    are merged with the same reciprocal-rank fusion Ask uses, so a page the
    question names can lead even when its text embeds as numbers. ``None``
    scores (unmeasurable) keep the given order -- newest document first.
    """
    if scores is None:
        return list(tables)[:limit]
    if not keywords:
        scored = [(scores.get(t.document_id or "", 0.0), i, t) for i, t in enumerate(tables)]
        ordered = sorted(scored, key=lambda s: (s[0] < floor, -s[0], s[1]))
        return [t for _, _, t in ordered[:limit]]
    from ..config.settings import DEFAULT_RETRIEVAL_RRF_K

    docs: list[str] = []
    seen: set[str] = set()
    for table in tables:
        doc = table.document_id or ""
        if doc not in seen:
            seen.add(doc)
            docs.append(doc)
    by_cosine = sorted(docs, key=lambda d: scores.get(d, 0.0), reverse=True)
    by_keyword = sorted(
        (d for d in docs if keywords.get(d, 0.0) > 0),
        key=lambda d: keywords[d], reverse=True,
    )
    fused: dict[str, float] = {}
    for leg in (by_cosine, by_keyword):
        for place, doc in enumerate(leg):
            fused[doc] = fused.get(doc, 0.0) + 1.0 / (DEFAULT_RETRIEVAL_RRF_K + place + 1)
    scored = [(fused.get(t.document_id or "", 0.0), i, t) for i, t in enumerate(tables)]
    ordered = sorted(scored, key=lambda s: (-s[0], s[1]))
    return [t for _, _, t in ordered[:limit]]


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


def _phrase(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def pick_named(table: TableRef, question: str, requested: str | None) -> TablePick:
    """A chart of the one table the question named, when the model did not pick it.

    A column header is used when that header is a whole phrase in the
    question. A single column of that type is used when the question names
    none. Two unnamed candidates are refused, with their names.
    """
    asked = f" {_phrase(question)} "

    def choose(types: tuple[str, ...], role: str) -> str | None:
        cols = [c for c in table.columns if c.get("type") in types]
        named = [
            c for c in cols
            if len(_phrase(str(c.get("name", ""))).replace(" ", "")) >= 3
            and f" {_phrase(str(c.get('name', '')))} " in asked
        ]
        if len(named) == 1:
            return named[0]["key"]
        if len(named) > 1:
            raise TableRefusal(
                f"\"{table.document_title}\" has more than one {role}: "
                + ", ".join(c["name"] for c in named) + "."
            )
        if len(cols) == 1:
            return cols[0]["key"]
        if len(cols) > 1:
            raise TableRefusal(
                f"I can see \"{table.document_title}\", but not which {role} to use. "
                + _options(table, types, role)
            )
        return None

    group_by = choose(("category",), "breakdown") or choose(("date",), "breakdown")
    value = choose(("number",), "value to add up")
    if group_by is None and value is None:
        raise TableRefusal(
            f"I can see \"{table.document_title}\", but it has no column to chart. "
            + _options(table, ("category", "date", "number"), "chart")
        )
    measure = "sum" if value else "count"
    chart = table_shape(table, group_by, None, requested)
    return TablePick(
        table_id=table.id, measure=measure, value=value, group_by=group_by,
        split_by=None, filters=(), chart=chart,
    )


def table_shape(table: TableRef, group_by: str | None, split_by: str | None,
                requested: str | None) -> str:
    """The requested shape when this table's form allows it, else the default.

    A date breakdown is a time axis, with the split (if any) as its series;
    otherwise each breakdown counts (``registry.shapes_for``)."""
    from .registry import shapes_for

    timed = bool(group_by) and (table.column(group_by) or {}).get("type") == "date"
    if timed:
        allowed = shapes_for(time=True, breakdowns=1 if split_by else 0)
    else:
        allowed = shapes_for(time=False, breakdowns=bool(group_by) + bool(split_by))
    return requested if requested in allowed else allowed[0]


def prose_chart(table: TableRef) -> TablePick | None:
    """The one chart of a table just read from sentences, or None.

    One number column. When a second category is a period (Q1, 2026), the
    other category is the series, which is a grouped bar. Two number columns
    are not guessed between.
    """
    from ..doctables.extract import PERIOD_TOKEN

    categories = [c for c in table.columns if c.get("type") == "category"]
    dates = [c for c in table.columns if c.get("type") == "date"]
    numbers = [c for c in table.columns if c.get("type") == "number"]
    if len(numbers) != 1 or len(categories) + len(dates) > 2:
        return None
    if not categories and not dates:
        return None

    def period_key(cols: list[dict]) -> str | None:
        best = None
        best_hits = 0
        for column in cols:
            samples = [str(s).strip() for s in (column.get("samples") or []) if str(s).strip()]
            if not samples:
                continue
            hits = sum(1 for sample in samples if PERIOD_TOKEN.match(re.sub(r"\s+", "", sample)))
            if hits >= 1 and hits / len(samples) >= 0.5 and hits > best_hits:
                best, best_hits = column["key"], hits
        return best

    group = dates[0]["key"] if len(dates) == 1 else period_key(categories)
    if group is None and len(categories) == 1 and not dates:
        group = categories[0]["key"]
    if group is None:
        return None
    rest = [c["key"] for c in categories + dates if c["key"] != group]
    if len(rest) > 1:
        return None
    return TablePick(
        table_id=table.id, measure="sum", value=numbers[0]["key"], group_by=group,
        split_by=rest[0] if rest else None, filters=(), chart="bar",
    )


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

    requested = data.get("chart") if isinstance(data.get("chart"), str) else None
    if group_by is None and requested == "line":
        # A line IS a time axis, and in a table the time axis is its date
        # column: "revenue per month as a line" drew one bar, the total,
        # because the period rode on nothing. Only when there is exactly one
        # date column -- with two, which one is the asker's call.
        dates = [c["key"] for c in table.columns if c.get("type") == "date"]
        if len(dates) == 1:
            group_by = dates[0]
    chart = table_shape(table, group_by, split_by, requested)
    return TablePick(
        table_id=table.id, measure=measure, value=value, group_by=group_by,
        split_by=split_by, filters=tuple(sorted(filters)), chart=chart,
    )
