"""Figures written in SENTENCES, read by an AI and checked by code.

"Q1 revenue was ₹12L, Q2 grew to ₹15L" is data nobody put in a table, and no
parser can turn prose into rows: knowing that ₹15L is Q2's revenue is reading
comprehension. So an AI reads it -- inline at INGEST on the background model
budget, and in Chart mode for a page ingest did not read -- and lists every
FACT: one figure, what it measures, its unit, period and item, and the exact
sentence. The AI never designs a table: code groups the facts into one table
per measure (and unit), with a column for the period and one per kind of item,
so every read of a page has the same shape and two measures are never added.

The AI is not trusted with the result. Code keeps a row only when:

- its quote appears in the document (whitespace- and case-insensitive),
- EVERY cell appears in that quote: a number must equal a number written in
  the quote (₹15L == 1,500,000), a label must occur in it verbatim, and
- the cells bind: each label to its own figure ("140 in January, 150 in June"
  -- one sentence, one convention, label after or before its figure), and
  each figure to its own measure when the measure is written in the quote.
  A figure another measure governs ("revenue was ₹40L while headcount
  reached 150": 150 is not revenue) is dropped, never moved.

So an invented figure, a changed figure, or a figure moved onto the wrong
label ("Q3" on a sentence about Q2, revenue plotted as headcount) fails the
check and is dropped. What the check cannot catch is a figure the AI MISSED
-- the chart says it was read from text, and a dropped row is named so a
partial series is not presented as complete.

Bounded: the whole document (pipe tables removed -- the table adapter already
has those, exactly) in sections of ``max_chars``, at most ``MAX_SECTIONS``
model calls, ``MAX_TABLES`` measures and ``MAX_ROWS`` facts each; anything
beyond is named on the chart.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, replace

from ..core.exceptions import ProviderError
from ..insights.describe import split_header
from ..security.untrusted import UNTRUSTED_POLICY, UNTRUSTED_REMINDER, scrub_untrusted_text
from . import extract
from .base import DatasetAdapter, DocumentText

logger = logging.getLogger(__name__)

ORIGIN = "text"
MAX_TABLES = 40
MAX_ROWS = 200
#: Model calls per document: a long page is read section by section, at
#: paragraph boundaries, rather than cut off.
MAX_SECTIONS = 6
#: Kinds of item a measure is broken down by (Plant, Product); a chart draws two.
MAX_KINDS = 2
MAX_COLUMNS = 8
#: A reasoning model spends a small cap before it writes the JSON. At 1500,
#: gemini-3.1-flash-lite logged `llm.truncated` with the whole budget used
#: and the page was left with no table. The call stops when the JSON is done.
#: A section's facts are verbose JSON (~60 tokens each); 16000 holds ~200.
MAX_TOKENS = 16000
#: How many figures a page needs before ingest spends a model call on it, and
#: (``MIN_CHART_NUMBERS``) before a page someone is waiting on is read. A
#: figure is any number that is not a year; no list of currencies, units or
#: measure words decides it, so "we hired 12 in Q1 and 18 in Q2" counts as
#: much as "₹12L". What the model returns is still checked against the text.
MIN_FIGURES = 3
MIN_CHART_NUMBERS = 2

TEXT_NOTE = (
    "Figures read from sentences in this document by AI. Each value was checked "
    "against the sentence it came from (shown on hover); a figure the AI missed "
    "is not counted."
)

#: Anything a figure can be written as, for comparing a cell to its quote.
#: A format parser (₹ / lakh / k / %), not intent: it reads a number, it does
#: not decide whether the page is worth charting.
_NUMBER_TOKEN = re.compile(
    r"(?<!\w)\(?-?(?:[₹$€£]|rs\.?|inr|usd|eur|gbp)?\s*\d[\d,\s]*(?:\.\d+)?\s*"
    r"(?:k|mn|m|bn|b|lakhs?|lac|l|crores?|cr)?\b\s*%?\)?",
    re.I,
)
_PIPE_LINE = re.compile(r"^\s*\|.*\|\s*$")


def _prose(content: str) -> str:
    """The document minus its pipe tables, which the table adapter reads exactly."""
    return "\n".join(l for l in (content or "").splitlines() if not _PIPE_LINE.match(l))


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[‐-―−]", "-", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _numbers_in(quote: str) -> list[float]:
    out = []
    for m in _NUMBER_TOKEN.finditer(quote):
        value, _ = extract.parse_number(m.group(0).strip())
        if value is not None:
            out.append(value)
    return out


def _close(value: float, other: float) -> bool:
    return abs(value - other) <= 1e-6 * max(1.0, abs(other))


def _cell_in_quote(cell: str, quote_norm: str, quote_numbers: list[float]) -> bool:
    cell = (cell or "").strip()
    if not cell:
        return True  # an empty cell claims nothing
    value, _ = extract.parse_number(cell)
    if value is not None:
        return any(_close(value, n) for n in quote_numbers)
    return _norm(cell) in quote_norm


#: A word long enough to name a measure. Between a measure and a figure that
#: follows an earlier figure, such a word ("while headcount reached") means
#: the measure has changed. Short glue ("in", "and", "to", "rose") does not.
#: Length, not a vocabulary: it reads the same in any language.
_LONG_WORD = re.compile(r"[^\W\d_]{5,}")


def _header_words(header: str) -> list[str]:
    """The words of a column's name, its bracketed unit removed."""
    name, _unit = split_header(header or "")
    return [w for w in _norm(name).split() if len(w) >= 3]


def _header_pattern(header: str) -> str | None:
    words = _header_words(header)
    if not words:
        return None
    parts = []
    for word in words:
        stem = word[:-1] if len(word) > 4 and word.endswith("s") else word
        parts.append(re.escape(stem) + "s?")
    return r"\b" + r"\W+".join(parts) + r"\b"


def _header_spans(header: str, text: str) -> list[tuple[int, int]]:
    pattern = _header_pattern(header)
    if pattern is None:
        return []
    return [(m.start(), m.end()) for m in re.finditer(pattern, text, re.I)]


def _is_year(value: float) -> bool:
    return value == int(value) and 1900 <= int(value) <= 2099


def _label_spans(label: str, text: str) -> list[tuple[int, int]]:
    needle = _norm(label)
    if not needle:
        return []
    if re.fullmatch(r"[a-z0-9]+", needle):
        pattern = rf"\b{re.escape(needle)}\b"
    else:
        pattern = re.escape(needle)
    return [(m.start(), m.end()) for m in re.finditer(pattern, text)]


def _num_spans(text: str) -> list[tuple[float, int, int]]:
    out = []
    for match in _NUMBER_TOKEN.finditer(text):
        value, _unit = extract.parse_number(match.group(0).strip())
        if value is not None:
            out.append((value, match.start(), match.end()))
    return out


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _is_label(header: str, cell: str, quote: str) -> bool:
    """A cell that names the row (Q2, North, June, 2024) rather than a figure.
    A year is a label unless its own header is a measure written in the quote."""
    value, _unit = extract.parse_number(cell)
    # A year is written as four bare digits: "1,900 units" is a figure.
    year = re.fullmatch(r"\d{4}", cell.strip()) is not None and _is_year(value or 0)
    return value is None or (year and not _header_spans(header, quote))


def _bound(span, nums, *, follows: bool):
    """The figure a label belongs to: the one just before it ("140 in
    January") when labels follow their figures, the one just after it ("Q2
    closed at ₹15L") when they lead. The other side only when there is none."""
    before = [n for n in nums if n[2] <= span[0]]
    after = [n for n in nums if n[1] >= span[1]]
    first, second = (before[-1:], after[:1]) if follows else (after[:1], before[-1:])
    return (first or second or [None])[0]


def _clean_stretch(stretch: str, allowed: set[str], names: list[str]) -> bool:
    """Nothing between an earlier figure and this one names another measure.

    When the row's own label opens the clause ("..., and Plant C contributed
    1,700"), the list item is that label, a verb and its figure: what follows
    the label may hold one connecting word. Otherwise every long word must be
    a label, a column name or a unit ("while headcount reached 150" fails).
    """
    last = -1
    for name in names:
        for _start, end in _label_spans(name, stretch):
            last = max(last, end)
    if last >= 0:
        extra = [w for w in _LONG_WORD.findall(stretch[last:]) if w not in allowed]
        return len(extra) <= 1
    return all(w in allowed for w in _LONG_WORD.findall(stretch))


def _measure_binds(header, value, measures, quote, nums, allowed, names=()) -> bool:
    """The figure belongs to this column's measure, not to another one.

    No constraint when the column's name is not written in the quote ("In Q1
    we brought in ₹12 lakh" under Revenue: the section names the measure, or
    the model named it). The figure is still bound to its label. When
    it is, the measure governing the figure -- the nearest measure written
    before it among the table's own columns, else the nearest after -- must be
    this one. And when other figures sit between the measure and this one
    ("spend was $40k in April, $55k in May"), the stretch after the last of
    them must not name something else ("revenue was ₹40L while headcount
    reached 150": 150 is not revenue).
    """
    own = _header_spans(header, quote)
    if not own:
        return True
    rivals = [s for other in measures if other != header for s in _header_spans(other, quote)]
    for _v, start, end in [n for n in nums if _close(n[0], value)]:
        mentions = [(s, True) for s in own] + [(s, False) for s in rivals]
        before = [m for m in mentions if m[0][1] <= start]
        after = [m for m in mentions if m[0][0] >= end]
        if before:
            governing, is_own = max(before, key=lambda m: m[0][1])
            gap = (governing[1], start)
            between = [n for n in nums if n[1] >= gap[0] and n[2] <= gap[1]]
            stretch = quote[between[-1][2]:start] if between else ""
        elif after:
            governing, is_own = min(after, key=lambda m: m[0][0])
            gap = (end, governing[0])
            between = [n for n in nums if n[1] >= gap[0] and n[2] <= gap[1]]
            stretch = quote[end:between[0][1]] if between else ""
        else:
            continue
        if is_own and _clean_stretch(stretch, allowed, list(names)):
            return True
    return False


def _opens_alone(cell: str, others: set[str], quote: str) -> bool:
    """The label opens a sentence of the quote ("In January, ...") and no
    other value of its column is in the quote: it is the context of every
    figure there, not bound to the nearest one."""
    starts = [0] + [m.end() for m in re.finditer(r"[.!?]\s+", quote)]
    opens = any(
        start - max(s for s in starts if s <= start) <= _OPENING_CHARS
        for start, _end in _label_spans(cell, quote)
    )
    want = _norm(cell)
    return opens and not any(
        _label_spans(v, quote) for v in others if _norm(v) != want
    )


def _bind_group(columns: list[str], rows: list[list[str]], quote: str,
                column_values: dict[int, set[str]] | None = None) -> list[bool]:
    """Which of these rows (all quoting ``quote``) bind cell to cell.

    Each figure binds to its own measure (``_measure_binds``), and each label
    to its own figure. Rows of one sentence follow one convention -- labels
    after their figures or before them -- so the convention that binds the
    most rows is the sentence's, and a row it does not bind is dropped (a tie
    keeps only the rows both readings bind). A
    label every row of the sentence shares ("In 2024, ...") is context, not
    a binding. A mismatch is dropped, never repaired.
    """
    # A label written before the sentence (``_context_label``) was checked
    # there; only labels inside the quote bind to a figure in it.
    label_cells = [
        [(h, c) for h, c in zip(columns, cells)
         if c.strip() and _is_label(h, c, quote) and _label_spans(c, quote)]
        for cells in rows
    ]
    label_spans = [s for labels in label_cells for _h, c in labels for s in _label_spans(c, quote)]
    nums = [n for n in _num_spans(quote) if not any(_overlaps((n[1], n[2]), s) for s in label_spans)]
    # Words a stretch may hold without naming another measure: the rows'
    # labels, the column names, and the word right after each figure (its
    # unit, whatever the language: "40 million", "150 Mitarbeiter").
    allowed = {m.group(1) for m in re.finditer(r"\d\S*\s+([^\W\d_]+)", quote)}
    for labels in label_cells:
        allowed |= {w for _h, c in labels for w in _norm(c).split()}
    for header in columns:
        allowed |= set(_header_words(header)) | set(_norm(header).split())
    shared = {(h, _norm(c)) for h, c in label_cells[0]} if len(rows) > 1 else set()
    for labels in label_cells[1:]:
        shared &= {(h, _norm(c)) for h, c in labels}

    values_by_row = [
        [(h, extract.parse_number(c)[0]) for h, c in zip(columns, cells)
         if c.strip() and not _is_label(h, c, quote)]
        for cells in rows
    ]
    # Only number columns compete for a figure; "Region" is not a measure.
    measures = sorted({h for values in values_by_row for h, _v in values})
    measured = []
    figures = []
    for values, labels in zip(values_by_row, label_cells):
        figures.append([v for _h, v in values])
        names = [c for _h, c in labels]
        measured.append(bool(values) and all(
            _measure_binds(h, v, measures, quote, nums, allowed, names) for h, v in values
        ))

    def binds(index: int, follows: bool) -> bool:
        for header, cell in label_cells[index]:
            if (header, _norm(cell)) in shared:
                continue
            others = (column_values or {}).get(columns.index(header), set())
            if _opens_alone(cell, others, quote):
                continue
            spans = _label_spans(cell, quote)
            hits = [_bound(s, nums, follows=follows) for s in spans]
            if not any(h and any(_close(h[0], v) for v in figures[index]) for h in hits):
                return False
        return True

    lead = [measured[i] and binds(i, False) for i in range(len(rows))]
    trail = [measured[i] and binds(i, True) for i in range(len(rows))]
    if sum(lead) != sum(trail):
        return lead if sum(lead) > sum(trail) else trail
    # Neither reading of the sentence wins: keep only what both agree on.
    return [a and b for a, b in zip(lead, trail)]


#: How far into a sentence a value still OPENS it ("In March, ...").
_OPENING_CHARS = 12


def _context_label(cell: str, values: set[str], quote_norm: str, paragraphs: list[str]) -> bool:
    """A label the sentence leaves to an earlier one in its paragraph.

    "In January, the facility produced 6,200 units. Plant A manufactured 2,400
    units" -- the month is not in the second sentence. It is kept only when it
    is the LAST of its column's values written before the quote in that
    paragraph -- preferring one that opens its sentence -- so "January" on a
    February sentence is still dropped.
    """
    want = _norm(cell)
    for paragraph in paragraphs:
        at = paragraph.find(quote_norm)
        if at < 0:
            continue
        before = paragraph[:at]
        # Where each sentence before the quote starts: a value in a sentence's
        # opening words ("In March, ...") sets the context; one in passing
        # ("compared with 19 hours in February") does not, unless no sentence
        # opens with one.
        starts = [0] + [m.end() for m in re.finditer(r"[.!?]\s+", before)]
        last = lead = None
        last_at = lead_at = -1
        for value in values:
            for start, _end in _label_spans(value, before):
                if start > last_at:
                    last, last_at = _norm(value), start
                opening = max(s for s in starts if s <= start)
                if start - opening <= _OPENING_CHARS and start > lead_at:
                    lead, lead_at = _norm(value), start
        return (lead or last) == want
    return False


def verify_rows(
    document: str, rows: list[dict], columns: list[str] | None = None,
) -> list[tuple[list[str], str]]:
    """``(cells, quote)`` for each row whose quote is in the document, whose
    every cell is in its quote -- or, for a label, written just before it in
    the same paragraph (``_context_label``) -- and (with ``columns``) whose
    cells bind to each other (``_bind_group``).

    A mismatch is dropped, never repaired. ``columns`` is omitted only by tests
    of the quote check itself; extraction always passes them.
    """
    doc_norm = _norm(document)
    paragraphs = [_norm(line) for line in (document or "").splitlines() if line.strip()]
    header = [str(c) for c in columns[:MAX_COLUMNS]] if columns else None
    # Every value the model gave each column: the candidates a context label
    # is chosen among.
    column_values: dict[int, set[str]] = {}
    for row in rows[:MAX_ROWS]:
        cells = row.get("cells") if isinstance(row, dict) else None
        for i, cell in enumerate(cells if isinstance(cells, list) else []):
            text = str(cell or "").strip()
            if text and extract.parse_number(text)[0] is None:
                column_values.setdefault(i, set()).add(text)
    checked: list[tuple[list[str], str, str]] = []
    for row in rows[:MAX_ROWS]:
        if not isinstance(row, dict):
            continue
        cells, quote = row.get("cells"), row.get("quote")
        if not isinstance(cells, list) or not isinstance(quote, str):
            continue
        cells = [str(c) if c is not None else "" for c in cells[:MAX_COLUMNS]]
        quote_norm = _norm(quote)
        if len(quote_norm) < 6 or len(quote_norm) > 400 or quote_norm not in doc_norm:
            continue
        numbers = _numbers_in(quote)
        if not all(
            _cell_in_quote(c, quote_norm, numbers)
            or (extract.parse_number(c)[0] is None
                and _context_label(c, column_values.get(i, set()), quote_norm, paragraphs))
            for i, c in enumerate(cells)
        ):
            continue
        if header is not None and len(cells) > len(header):
            continue
        checked.append((cells, quote.strip(), quote_norm))
    if header is None:
        return [(cells, quote) for cells, quote, _ in checked]
    keep = [False] * len(checked)
    groups: dict[str, list[int]] = {}
    for index, (_cells, _quote, quote_norm) in enumerate(checked):
        groups.setdefault(quote_norm, []).append(index)
    for quote_norm, indexes in groups.items():
        verdicts = _bind_group(header, [checked[i][0] for i in indexes], quote_norm,
                               column_values)
        for i, ok in zip(indexes, verdicts):
            keep[i] = ok
    return [(cells, quote) for (cells, quote, _), ok in zip(checked, keep) if ok]


def build_prompt(title: str, text: str) -> str:
    """The prompt. The document is outside text: policy before, reminder after."""
    fenced = scrub_untrusted_text(text)
    return (
        "List EVERY figure written in the sentences of this document as a fact.\n\n"
        "Rules:\n"
        "- One fact per number the text states. Never compute, total, convert or "
        "guess. Skip numbers that measure nothing (a phone number, a page number, "
        "a date on its own).\n"
        "- \"measure\": what the number measures, in the text's own words "
        "(production, waste, tickets resolved, customer satisfaction). Use the SAME "
        "words for every fact of the same measure.\n"
        "- \"unit\": the unit written with the number (units, kg, tonnes, hours, "
        "%, ₹), or \"\".\n"
        "- \"period\": the period the number is for, as written (January, Q2, "
        "2025, end of April), or \"\". It may come from an earlier sentence of the "
        "same paragraph (\"In January, ... . Plant A made 2,400\": January).\n"
        "- \"items\": what the number belongs to, as {\"<kind>\": \"<name>\"} "
        "(Plant: Plant A, Product: Alpha, Region: North), or {}. Use the same kind "
        "and the same name for the same thing in every fact: when the text writes a "
        "longer and, elsewhere, a shorter name ON ITS OWN, use the shorter one.\n"
        "- A total over several periods (the first four months, year to date) has "
        "that span as its period and \"total\": true.\n"
        "- \"value\": the number exactly as written (\"2,400\", \"₹15L\", "
        "\"76%\").\n"
        "- \"quote\": the exact sentence the number is in, copied character for "
        "character.\n"
        "- If there are no figures, return {\"facts\": []}.\n\n"
        "Reply with ONLY a JSON object:\n"
        '{"facts": [{"measure": "...", "unit": "...", "period": "...", '
        '"items": {"<kind>": "<name>"}, "total": false, "value": "...", "quote": "..."}]}\n\n'
        f"{UNTRUSTED_POLICY}"
        "<<<UNTRUSTED_DOCUMENT>>>\n"
        f"Title: {scrub_untrusted_text(title)[:200]}\n\n{fenced}\n"
        "<<<END_UNTRUSTED_DOCUMENT>>>\n"
        f"{UNTRUSTED_REMINDER}"
    )


_JSON = re.compile(r"\{.*\}", re.S)


_NUMBER_THEN_WORD = re.compile(r"(.*\d)\s*([^\W\d_]{1,16})\.?")


def _split_units(header: list[str], rows: list[dict]) -> tuple[list[str], dict]:
    """"18.5 tonnes" -> "18.5", whatever the word after the number is.

    The number parser knows formats (₹, lakh, k, %), not every unit a page can
    use, so a cell like "420 kg" read as text and its row had no figure. The
    unit is moved, never dropped: into the column name when the whole column
    shares it ("Figure (tonnes)"), else onto that row's own label afterwards
    (``_carry_units``), so tonnes and kilograms are never one series. Rows are
    changed in place; returns the header and ``{(cells, quote): unit}``.
    """
    per_row: dict[int, str] = {}
    column_unit: dict[int, set[str]] = {}
    #: Figures per column that already parsed ("76%"): a column where some do
    #: is not wholly in the word unit the others carry ("4.2 kWh").
    plain: dict[int, int] = {}
    for index, row in enumerate(rows):
        cells = row.get("cells")
        if not isinstance(cells, list):
            continue
        for i, cell in enumerate(cells[:len(header)]):
            text = str(cell or "").strip()
            if text and extract.parse_number(text)[0] is not None:
                plain[i] = plain.get(i, 0) + 1
                continue
            if not text:
                continue
            match = _NUMBER_THEN_WORD.fullmatch(text)
            if not match or extract.parse_number(match.group(1))[0] is None:
                continue
            cells[i] = match.group(1).strip()
            column_unit.setdefault(i, set()).add(match.group(2))
            per_row[index] = match.group(2)
    header = list(header)
    shared = {i: next(iter(u)) for i, u in column_unit.items() if len(u) == 1 and not plain.get(i)}
    for i, unit in shared.items():
        name, bracket = split_header(header[i])
        if not bracket:
            header[i] = f"{header[i]} ({unit})"
    units = {}
    if len(shared) != len(column_unit):
        for index, unit in per_row.items():
            cells = rows[index].get("cells") or []
            units[(tuple(str(c) if c is not None else "" for c in cells), str(rows[index].get("quote", "")).strip())] = unit
    return header, units


def _merge_labels(rows, header: list[str]):
    """One name per item: "Product Alpha" in January and "Alpha" after it are
    one product, or the chart draws six series for three. A label is shortened
    only to ANOTHER label in its column, by dropping the column's own name
    ("Product"), so "Plant A" stays "Plant A" when no "A" was written."""
    for i, name in enumerate(header):
        words = _norm(name).split()
        if not words:
            continue
        values: dict[str, str] = {}
        for cells, _ in rows:
            if i < len(cells) and cells[i]:
                values.setdefault(_norm(cells[i]), cells[i])
        for cells, _ in rows:
            if i >= len(cells) or not cells[i]:
                continue
            # "raw material consumption" and "Raw material consumption" too.
            cells[i] = values[_norm(cells[i])]
            parts = _norm(cells[i]).split()
            for short in (parts[len(words):] if parts[:len(words)] == words else None,
                          parts[:-len(words)] if parts[-len(words):] == words else None):
                if short and " ".join(short) in values:
                    cells[i] = values[" ".join(short)]
                    break
    return rows


def _carry_units(rows, units: dict, header: list[str]):
    """Put a row's own unit on its last label ("production waste (kg)"). A row
    whose unit has no label to sit on is dropped: a figure without its unit
    beside figures in another unit would be added to them."""
    if not units:
        return rows
    out = []
    for cells, quote in rows:
        unit = units.get((tuple(cells), quote))
        if unit is None:
            out.append((cells, quote))
            continue
        labels = [i for i, c in enumerate(cells) if c.strip() and extract.parse_number(c)[0] is None]
        if not labels:
            continue
        cells = list(cells)
        cells[labels[-1]] = f"{cells[labels[-1]]} ({unit})"
        out.append((cells, quote))
    return out


class TextAdapter(DatasetAdapter):
    origin = ORIGIN

    def __init__(self, llm=None, *, max_chars: int = 12000, interactive: bool = False):
        """``llm=None`` is ingest: the background (aux) model, after a
        background slot is free. ``interactive`` is a read someone is waiting
        on (Chart mode, an upload): a looser figures check."""
        self._llm = llm
        self._max_chars = max_chars
        self._interactive = interactive

    def wants(self, doc: DocumentText) -> bool:
        """Cheap: enough figures in the prose to be worth a model call.

        ``MIN_FIGURES`` at ingest, ``MIN_CHART_NUMBERS`` when someone is
        waiting. Any number but a year counts; the AI's rows are checked
        against their sentences either way."""
        if doc.tables:
            # A Sheet or CSV: its rows ARE the data, read exactly already.
            return False
        prose = _prose(doc.content)
        figures = [v for v, _s, _e in _num_spans(prose) if not _is_year(v)]
        return len(figures) >= (MIN_CHART_NUMBERS if self._interactive else MIN_FIGURES)

    def _model(self):
        if self._llm is not None:
            return self._llm
        from ..llm.factory import build_aux_llm_provider
        from ..llm.pacing import wait_for_background_slot

        if not wait_for_background_slot():
            raise ProviderError("text adapter: no background model slot came free")
        return build_aux_llm_provider()

    def extract(self, doc: DocumentText) -> list[extract.Table]:
        prose = _prose(doc.content)
        sections = _sections(prose, self._max_chars)
        unread = len(sections) - MAX_SECTIONS
        llm = self._model()
        facts: list[dict] = []
        for index, section in enumerate(sections[:MAX_SECTIONS]):
            if index and self._llm is None:
                from ..llm.pacing import wait_for_background_slot

                # Ingest: every call waits its turn, so chat keeps its reserve.
                if not wait_for_background_slot():
                    raise ProviderError("text adapter: no background model slot came free")
            facts.extend(self._read(llm, doc, section))
        notes = []
        if unread > 0:
            notes.append(f"Only the first {MAX_SECTIONS} sections of this page were read.")
        return facts_to_tables(prose, facts, extra_notes=notes)

    def _read(self, llm, doc: DocumentText, section: str) -> list[dict]:
        try:
            reply = llm.generate(build_prompt(doc.title, section), max_tokens=MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001 - our type, the caller decides
            raise ProviderError("text adapter: model call failed", cause=exc) from exc
        if getattr(llm, "last_truncated", False):
            # A cut reply is not "this page has no figures": never marked read.
            raise ProviderError("text adapter: the reply was cut off before the facts were finished")
        match = _JSON.search(reply or "")
        try:
            data = json.loads(match.group(0)) if match else {}
        except (ValueError, TypeError):
            data = {}
        found = data.get("facts") if isinstance(data, dict) else None
        if not isinstance(found, list):
            logger.info("text adapter: no usable reply for %s", doc.external_id)
            return []
        return [f for f in found if isinstance(f, dict)]


def _sections(prose: str, size: int) -> list[str]:
    """The page in pieces of at most ``size`` characters, cut between
    paragraphs, so a long page is read whole and no sentence is split."""
    out: list[str] = []
    current = ""
    for line in prose.splitlines(keepends=True):
        while len(line) > size:  # one paragraph longer than a section
            if current:
                out.append(current)
                current = ""
            cut = line.rfind(". ", 0, size) + 1 or size  # at a sentence end if any
            out.append(line[:cut])
            line = line[cut:]
        if len(current) + len(line) > size and current:
            out.append(current)
            current = ""
        current += line
    if current.strip():
        out.append(current)
    return out


def _text(value) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:120]


_YEAR = re.compile(r"^(?:fy\s?)?(?:19|20)\d{2}$", re.I)


def _grain(fact: dict) -> str:
    """Which series a fact's period belongs to: years apart from months, and a
    total over a span apart from the periods it sums. Format only."""
    period = _norm(_text(fact.get("period")))
    if not period:
        return ""
    if fact.get("total") is True:
        return "total"
    return "year" if _YEAR.match(period) else "period"


def _items(fact: dict) -> dict[str, tuple[str, str]]:
    """``{normalized kind: (kind as written, name)}``."""
    items = fact.get("items") if isinstance(fact.get("items"), dict) else {}
    return {_norm(str(k)): (_text(k), _text(v)) for k, v in items.items() if _text(v)}


_WORD_AFTER = re.compile(r"\s*([^\W\d_]+)")


def _other_units_word(fact: dict, units: set[str]) -> bool:
    """True when every place the quote writes this figure, the word right after
    it is ANOTHER measure's unit: "waste per 1,000 units" is not 1,000 kg.
    The units are the page's own, read from its facts; no list decides it."""
    unit = _norm(_text(fact.get("unit")))
    if not unit.isalpha():
        return False
    wanted = extract.parse_number(_text(fact.get("value")))[0]
    quote = str(fact.get("quote") or "")
    after = [_WORD_AFTER.match(quote, end) for value, _start, end in _num_spans(quote)
             if wanted is not None and _close(value, wanted)]
    words = [_norm(m.group(1)) for m in after if m]
    return bool(words) and len(words) == len(after) and all(
        w in units and not (w.startswith(unit) or unit.startswith(w)) for w in words)


def facts_to_tables(prose: str, facts: list[dict], *, extra_notes=()) -> list[extract.Table]:
    """Tables built by code, never by the model: one per measure and unit, per
    what it is broken down by (Plant, Product) and per period grain (years,
    months, a span's total), so no two kinds of figure are ever added.

    A kind of item with one name across the whole measure ("support team") is
    the subject, not a breakdown, and is left out. Every row then goes through
    the same checks as before (``verify_rows``: quote in the page, cells in the
    quote, label bound to figure). Fewer than two kept figures is not a series.
    """
    units = {_norm(_text(f.get("unit"))) for f in facts if _norm(_text(f.get("unit"))).isalpha()}
    measures: dict[tuple[str, str], list[dict]] = {}
    for fact in facts:
        if _other_units_word(fact, units):
            continue
        measure, value = _text(fact.get("measure")), _text(fact.get("value"))
        if not measure or not value or not str(fact.get("quote") or "").strip():
            continue
        measures.setdefault((_norm(measure), _norm(_text(fact.get("unit")))), []).append(fact)

    groups: dict[tuple, list[dict]] = {}
    for key, group in measures.items():
        names: dict[str, set[str]] = {}
        for fact in group:
            for kind, (_shown, name) in _items(fact).items():
                names.setdefault(kind, set()).add(_norm(name))
        breakdowns = {kind for kind, seen in names.items() if len(seen) > 1}
        for fact in group:
            kinds = tuple(sorted(k for k in _items(fact) if k in breakdowns))[:MAX_KINDS]
            groups.setdefault((*key, kinds, _grain(fact)), []).append(fact)

    out: list[extract.Table] = []
    for (_m, _u, kinds, grain), group in list(groups.items())[:MAX_TABLES]:
        measure = _text(group[0].get("measure"))
        unit = _text(group[0].get("unit"))
        shown = {}
        for fact in group:
            for kind, (written, _name) in _items(fact).items():
                shown.setdefault(kind, written)
        figure = measure[:1].upper() + measure[1:]
        if unit and _norm(unit) not in _norm(measure):
            figure = f"{figure} ({unit})"
        period_name = {"year": "Year", "total": "Span"}.get(grain, "Period")
        header = ([period_name] if grain else []) + \
            [shown[k][:1].upper() + shown[k][1:] for k in kinds] + [figure]

        raw_rows, seen = [], set()
        for fact in group[:MAX_ROWS]:
            items = _items(fact)
            cells = ([_text(fact.get("period"))] if grain else []) + \
                [items[k][1] for k in kinds] + [_text(fact.get("value"))]
            quote = str(fact.get("quote") or "").strip()
            if (tuple(cells), quote) in seen:
                continue  # the same fact read twice
            seen.add((tuple(cells), quote))
            raw_rows.append({"cells": cells, "quote": quote})

        header, row_units = _split_units(header, raw_rows)
        rows = _merge_labels(
            _carry_units(verify_rows(prose, raw_rows, header), row_units, header), header)
        # Two different figures for the same period and item ("the same
        # period": 3.8% and 2.6%) cannot both be that point: neither is kept.
        values: dict[tuple, set] = {}
        for cells, _q in rows:
            values.setdefault(tuple(_norm(c) for c in cells[:-1]), set()).add(_norm(cells[-1]))
        rows = [(cells, q) for cells, q in rows
                if len(values[tuple(_norm(c) for c in cells[:-1])]) == 1]
        if len(rows) < extract.MIN_ROWS:
            continue
        notes = [TEXT_NOTE, *extra_notes]
        if len(raw_rows) > len(rows):
            notes.append(
                f"{len(raw_rows) - len(rows)} figure(s) were left out because the "
                "number did not bind to its label or measure in its sentence."
            )
        if len(group) > MAX_ROWS:
            notes.append(f"Only the first {MAX_ROWS} figures of this measure were read.")
        if len(groups) > MAX_TABLES:
            notes.append(f"{len(groups) - MAX_TABLES} more series in this document were not read.")
        name = figure
        if kinds:
            name += " by " + " and ".join(header[1 if grain else 0:-1])
        name += {"year": " by year", "total": " (totals)"}.get(grain, "")
        table = extract.profile(extract.RawTable(
            name=name[:200], header=header,
            rows=[cells for cells, _ in rows], notes=tuple(notes),
        ))
        if table is None or not any(c.type == "number" for c in table.columns):
            continue  # nothing to add up is not a chart
        out.append(replace(table, quotes=tuple(q for _, q in rows)))
    return out


#: Pages whose sentences are read while someone waits in Chart mode.
MAX_CHART_READS = 2


@dataclass(frozen=True)
class ProseRead:
    """Pages that gained a figure table, and pages whose sentences had none."""

    document_ids: tuple[str, ...]
    empty_titles: tuple[str, ...]


def read_for_chart(
    *, org_id: str, workspace_id: str | None, viewer, question: str,
    providers: list[str] | None, llm, tag: str | None = None,
) -> ProseRead:
    """Read figures from at most two pages whose text matches the question.

    Ingest reads pages with enough unit-bearing figures; this covers the rest
    (fewer or plainer figures, a page indexed before ingest read prose, a
    failed ingest read) because someone is waiting in Chart mode and nothing
    stored answers them. ``providers`` keeps the read on the connector the
    question names; ``tag`` on one Slack channel. Never raises: a failed read
    costs the chart, not the refusal.
    """
    from ..config.settings import DocTablesSettings

    from .queue import mark_read
    from .store import matching_prose, replace_document_tables

    if llm is None or viewer is None or getattr(viewer, "is_unrestricted", False):
        return ProseRead((), ())
    try:
        pages = matching_prose(
            org_id=org_id, workspace_id=workspace_id, viewer=viewer,
            question=question, providers=providers, tag=tag,
        )
    except Exception:  # noqa: BLE001
        logger.warning("text adapter: could not find pages to read", exc_info=True)
        return ProseRead((), ())
    adapter = TextAdapter(llm, max_chars=DocTablesSettings.from_env().text_max_chars,
                          interactive=True)
    saved: list[str] = []
    empty: list[str] = []
    calls = 0
    for document_id, external_id, title, text, truncated in pages:
        if calls >= MAX_CHART_READS:
            break
        doc = DocumentText(external_id=external_id or document_id, title=title, content=text)
        if not adapter.wants(doc):
            continue
        calls += 1
        try:
            tables = adapter.extract(doc)
        except ProviderError:
            logger.warning("text adapter: could not read %s", title, exc_info=True)
            continue
        if truncated and tables:
            note = "Only the first part of this page was read."
            tables = [replace(item, notes=item.notes + (note,)) for item in tables]
        if not tables:
            empty.append(title or "a page")
            try:
                mark_read(
                    document_id, org_id=org_id, workspace_id=workspace_id,
                    external_id=external_id or document_id, title=title or "",
                )
            except Exception:  # noqa: BLE001
                logger.warning("text adapter: could not remember a read of %s", title, exc_info=True)
            continue
        try:
            replace_document_tables(
                document_id, org_id=org_id, workspace_id=workspace_id,
                tables=tables, origin=ORIGIN,
            )
        except ProviderError:
            logger.warning("text adapter: could not store figures from %s", title, exc_info=True)
            continue
        saved.append(document_id)
        break  # the closest page produced a series; don't read another
    return ProseRead(tuple(saved), tuple(empty))
