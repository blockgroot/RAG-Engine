"""Tables found in documents, turned into typed rows.

Step 3 of docs/plans/2026-09-30-open-ended-charts.md. "Chart last year's
revenue by region" refused against a sales sheet sitting in Drive: charts
counted only ``activity_facts`` (who edited what, when), never what a document
SAYS. Numbers read back out of embedded chunk text are unfalsifiable, so the
fix is not to chart chunks -- it is to keep the table as ROWS, with each
column's type decided here, deterministically, and let SQL do the arithmetic.

Everything in this module is pure (no DB, no network, no model): parsing,
profiling and the one-paragraph description that is embedded in place of a
spreadsheet's cells.

Bounded on purpose (`MAX_*`): a 200k-row export must not become 200k inserts
inside one ingest job. Anything cut is marked ``truncated`` and says so on the
chart.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

MAX_TABLES_PER_DOCUMENT = 10
MAX_ROWS = 5000
MAX_COLUMNS = 30
#: A "table" of one row or one column is a list or a sentence with pipes in it.
MIN_ROWS = 2
MIN_COLUMNS = 2
#: A column is a number/date column only when this share of its non-empty
#: cells parse as one. Below that, one stray "N/A" would not be the problem --
#: a column of mostly words would be.
TYPE_SHARE = 0.8
#: A category column groups; a free-text column (notes, descriptions) does not.
MAX_CATEGORY_DISTINCT = 50

COLUMN_TYPES = ("number", "date", "category", "text")


@dataclass(frozen=True)
class RawTable:
    """A header and rows of strings, as the source gave them."""

    name: str
    header: list[str]
    rows: list[list[str]]
    truncated: bool = False
    #: Disclosures the source knows and the rows cannot show ("first tab only").
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Column:
    #: ``c0``, ``c1``, ... -- OUR identifier, the only thing ever spliced into
    #: SQL. The header text is a label and a lookup key, never an identifier.
    key: str
    name: str
    type: str
    unit: str = ""
    distinct: int = 0
    samples: tuple[str, ...] = ()
    #: Non-empty cells that did not parse as the column's type. They are
    #: stored raw and left out of any sum, and the chart caveat says how many.
    unparsed: int = 0

    def as_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "type": self.type,
            "unit": self.unit, "distinct": self.distinct,
            "samples": list(self.samples), "unparsed": self.unparsed,
        }


@dataclass(frozen=True)
class Table:
    """A profiled table: typed columns plus normalized and raw rows."""

    name: str
    columns: tuple[Column, ...]
    #: ``{key: value}`` per row -- a number is a JSON number, a date an ISO
    #: string, a category its trimmed text, an unparseable cell absent.
    cells: tuple[dict, ...]
    raw: tuple[tuple[str, ...], ...]
    truncated: bool = False
    notes: tuple[str, ...] = field(default=())
    #: Per row, the exact sentence it was read from (text adapter only).
    quotes: tuple[str | None, ...] = field(default=())


# --------------------------------------------------------------------------
# Finding tables
# --------------------------------------------------------------------------


def parse_csv(text: str, *, name: str) -> RawTable | None:
    """A CSV/TSV export (a Google Sheet's first tab, an uploaded .csv)."""
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        return None
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows: list[list[str]] = []
    truncated = False
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        if len(rows) > MAX_ROWS:  # header + MAX_ROWS data rows
            truncated = True
            break
        rows.append(row)
    if len(rows) < MIN_ROWS + 1:
        return None
    return RawTable(name=name, header=rows[0], rows=rows[1:], truncated=truncated)


_PIPE_ROW = re.compile(r"^\s*\|?(.+?\|.*?)\|?\s*$")
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")


def _split_pipe_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    # `\|` is an escaped pipe inside a cell, not a boundary.
    parts = re.split(r"(?<!\\)\|", body)
    return [p.replace("\\|", "|").strip() for p in parts]


def find_markdown_tables(text: str, *, title: str) -> list[RawTable]:
    """GitHub-style pipe tables: a header row, a ``---`` separator, rows.

    This is how Notion pages and Google Docs (exported as markdown) arrive,
    and how ``render_docx_tables`` writes Word tables, so one parser covers
    all three. Each table is named after the nearest heading above it, or
    the document title -- "Sales 2025 (table 2)" is still a name a person can
    recognise when choosing between tables.
    """
    lines = (text or "").splitlines()
    out: list[RawTable] = []
    heading = ""
    i = 0
    while i < len(lines) - 1 and len(out) < MAX_TABLES_PER_DOCUMENT:
        line = lines[i]
        found = _HEADING.match(line)
        if found:
            heading = found.group(1).strip()
        if "|" in line and _SEPARATOR.match(lines[i + 1]) and _PIPE_ROW.match(line):
            header = _split_pipe_row(line)
            rows: list[list[str]] = []
            truncated = False
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                if len(rows) >= MAX_ROWS:
                    truncated = True
                else:
                    rows.append(_split_pipe_row(lines[j]))
                j += 1
            if len(rows) >= MIN_ROWS and len(header) >= MIN_COLUMNS:
                number = len(out) + 1
                name = heading or (title if number == 1 else f"{title} (table {number})")
                out.append(RawTable(name=name[:200], header=header, rows=rows,
                                    truncated=truncated))
            i = j
            continue
        i += 1
    return out


def render_docx_tables(document) -> str:
    """Word tables as pipe tables, so ``find_markdown_tables`` reads them.

    ``python-docx``'s paragraphs skip tables entirely, so every table in a
    .docx was simply missing from the index -- both for Q&A and here.
    """
    blocks = []
    for table in list(getattr(document, "tables", []))[:MAX_TABLES_PER_DOCUMENT]:
        rows = []
        for row in table.rows[: MAX_ROWS + 1]:
            cells = [(c.text or "").replace("|", "\\|").replace("\n", " ").strip()
                     for c in row.cells]
            rows.append("| " + " | ".join(cells) + " |")
        if len(rows) >= MIN_ROWS + 1:
            width = rows[0].count("|") - 1
            rows.insert(1, "|" + " --- |" * max(width, 1))
            blocks.append("\n".join(rows))
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------
# Typing cells
# --------------------------------------------------------------------------

_CURRENCY = {
    "₹": "₹", "rs": "₹", "rs.": "₹", "inr": "₹",
    "$": "$", "usd": "$", "€": "€", "eur": "€", "£": "£", "gbp": "£",
}
_SCALE = {"k": 1e3, "m": 1e6, "mn": 1e6, "bn": 1e9, "b": 1e9,
          "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "cr": 1e7, "crore": 1e7, "crores": 1e7}
_NUMBER = re.compile(
    r"^(?P<neg>-)?(?P<cur1>[₹$€£]|rs\.?|inr|usd|eur|gbp)?\s*(?P<neg2>-)?"
    r"(?P<num>\d[\d,\s]*(?:\.\d+)?|\.\d+)\s*"
    r"(?P<scale>k|mn|m|bn|b|lakhs?|lac|l|crores?|cr)?\s*"
    r"(?P<cur2>[₹$€£]|inr|usd|eur|gbp)?\s*(?P<pct>%)?$",
    re.I,
)


def parse_number(value: str) -> tuple[float | None, str]:
    """``("₹1,20,000")`` -> ``(120000.0, "₹")``. ``(None, "")`` if not a number.

    Indian digit grouping, currency before or after, ``(1,200)`` accounting
    negatives, a trailing ``%`` and lakh/crore/k/m scales are all real in
    spreadsheets people share, and a sum that silently skips "₹1,20,000"
    because of its commas is a wrong total that looks right.
    """
    text = (value or "").strip()
    if not text:
        return None, ""
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    match = _NUMBER.match(text)
    if not match:
        return None, ""
    digits = re.sub(r"[,\s]", "", match.group("num"))
    try:
        number = float(digits)
    except ValueError:
        return None, ""
    scale = (match.group("scale") or "").lower()
    if scale == "l":
        # "₹12L" is lakh; a bare "12L" may be litres, so only with a currency.
        if not (match.group("cur1") or match.group("cur2")):
            return None, ""
        scale = "lakh"
    number *= _SCALE.get(scale, 1)
    if negative or match.group("neg") or match.group("neg2"):
        number = -number
    currency = (match.group("cur1") or match.group("cur2") or "").lower()
    unit = _CURRENCY.get(currency, "")
    if match.group("pct"):
        unit = "%"
    return number, unit


_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y",
    "%Y-%m", "%b %Y", "%B %Y", "%b-%Y", "%B-%Y", "%b-%y", "%b %y", "%m/%Y",
)


def _parse_date(value: str, fmt: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), fmt).date()
    except ValueError:
        return None


def _date_format(values: list[str]) -> str | None:
    """The ONE format that parses (nearly) the whole column, or None.

    Decided per column, not per cell, because "03/04/2025" means March in one
    sheet and April in another; a column of them is resolved by whichever
    reading fits every other cell. Day-first wins a tie -- it is the reading
    in most of the world, including India.
    """
    for fmt in _DATE_FORMATS:
        ok = sum(1 for v in values if _parse_date(v, fmt))
        if ok and ok >= TYPE_SHARE * len(values):
            return fmt
    return None


_YEAR = re.compile(r"^(19|20)\d{2}$")

#: What a spreadsheet writes for "no value". Left out when deciding a
#: column's type -- one "N/A" in a revenue column must not turn it into text
#: -- but still counted as unparsed, so the chart says a cell was skipped.
_PLACEHOLDERS = frozenset({"n/a", "na", "-", "--", "—", "–", "null", "none", "nil",
                           "tbd", "?", "nan", "#n/a"})


def profile(raw: RawTable) -> Table | None:
    """Type each column and normalize every cell. None if nothing is usable."""
    width = min(max(len(raw.header), max((len(r) for r in raw.rows), default=0)),
                MAX_COLUMNS)
    if width < MIN_COLUMNS:
        return None
    notes = list(raw.notes)
    if len(raw.header) > MAX_COLUMNS:
        notes.append(f"Only the first {MAX_COLUMNS} columns are kept.")
    header = [(raw.header[i] if i < len(raw.header) else "").strip() for i in range(width)]
    names, seen = [], {}
    for i, name in enumerate(header):
        name = re.sub(r"\s+", " ", name) or f"Column {i + 1}"
        base, n = name, seen.get(name.lower(), 0)
        if n:
            name = f"{base} ({n + 1})"
        seen[base.lower()] = n + 1
        names.append(name[:80])
    rows = [[(r[i] if i < len(r) else "").strip() for i in range(width)] for r in raw.rows]

    columns: list[Column] = []
    cells: list[dict] = [{} for _ in rows]
    for i in range(width):
        key = f"c{i}"
        present = [r[i] for r in rows if r[i]]
        values = [v for v in present if v.lower() not in _PLACEHOLDERS]
        if not values:
            continue
        numbers = [parse_number(v) for v in values]
        parsed_numbers = [n for n, _ in numbers if n is not None]
        is_year = (names[i].lower() in ("year", "fy", "yr")
                   or all(_YEAR.match(v) for v in values))
        date_fmt = "%Y" if is_year and all(_YEAR.match(v) for v in values) else None
        if date_fmt is None and len(parsed_numbers) < TYPE_SHARE * len(values):
            date_fmt = _date_format(values)

        if date_fmt:
            unparsed = 0
            for row_i, r in enumerate(rows):
                if not r[i]:
                    continue
                d = _parse_date(r[i], date_fmt)
                if d is None:
                    unparsed += 1
                else:
                    cells[row_i][key] = d.isoformat()
            columns.append(Column(key, names[i], "date", unparsed=unparsed,
                                  distinct=len(set(values)),
                                  samples=tuple(values[:3])))
            continue

        if len(parsed_numbers) >= TYPE_SHARE * len(values):
            units = {u for n, u in numbers if n is not None and u}
            unparsed = 0
            for row_i, r in enumerate(rows):
                if not r[i]:
                    continue
                n, _ = parse_number(r[i])
                if n is None:
                    unparsed += 1
                else:
                    cells[row_i][key] = n
            unit = units.pop() if len(units) == 1 else ""
            columns.append(Column(key, names[i], "number", unit=unit,
                                  unparsed=unparsed, distinct=len(set(values)),
                                  samples=tuple(values[:3])))
            continue

        distinct = sorted({v for v in values})
        kind = "category" if len(distinct) <= max(MAX_CATEGORY_DISTINCT, 0) else "text"
        for row_i, r in enumerate(rows):
            if r[i]:
                cells[row_i][key] = r[i][:200]
        columns.append(Column(key, names[i], kind, distinct=len(distinct),
                              samples=tuple(distinct[:5])))

    if len(columns) < MIN_COLUMNS:
        return None
    if raw.truncated:
        notes.append(f"Only the first {MAX_ROWS} rows are kept.")
    return Table(
        name=raw.name or "Untitled table",
        columns=tuple(columns),
        cells=tuple(cells),
        raw=tuple(tuple(r) for r in rows),
        truncated=raw.truncated,
        notes=tuple(notes),
    )


def describe(table: Table) -> str:
    """The text embedded for a spreadsheet, INSTEAD of its cells.

    Enough for search to find the sheet ("the sales sheet", "revenue by
    region") and for Q&A to say what it holds -- and deliberately no figures:
    a number read back out of chunk text is the unfalsifiable kind this whole
    step exists to replace. The chart path reads the rows.
    """
    parts = [f"Spreadsheet table \"{table.name}\" with {len(table.cells)} rows."]
    for c in table.columns:
        if c.type == "category":
            parts.append(f"Column {c.name}: categories such as {', '.join(c.samples)}.")
        elif c.type == "number":
            unit = f" ({c.unit})" if c.unit else ""
            parts.append(f"Column {c.name}: numbers{unit}.")
        elif c.type == "date":
            parts.append(f"Column {c.name}: dates.")
        else:
            parts.append(f"Column {c.name}: text.")
    parts.append("Ask for a chart of this table to see its figures.")
    return "\n".join(parts)
