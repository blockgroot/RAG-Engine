"""Figures written in SENTENCES, read by an AI and checked by code.

"Q1 revenue was ₹12L, Q2 grew to ₹15L" is data nobody put in a table, and no
parser can turn prose into rows: knowing that ₹15L is Q2's revenue is reading
comprehension. So an AI reads it -- inline at INGEST on the background model
budget, and in Chart mode for a page ingest did not read -- and returns small
tables where every row carries the exact sentence it came from.

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

Bounded: ``max_chars`` of the document (pipe tables removed -- the table
adapter already has those, exactly), at most ``MAX_TABLES`` tables of
``MAX_ROWS`` rows, and one model call per document.
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
MAX_TABLES = 5
MAX_ROWS = 200
MAX_COLUMNS = 8
#: A reasoning model spends a small cap before it writes the JSON. At 1500,
#: gemini-3.1-flash-lite logged `llm.truncated` with the whole budget used
#: and the page was left with no table. The call stops when the JSON is done.
MAX_TOKENS = 8000
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
    return value is None or (_is_year(value) and not _header_spans(header, quote))


def _bound(span, nums, *, follows: bool):
    """The figure a label belongs to: the one just before it ("140 in
    January") when labels follow their figures, the one just after it ("Q2
    closed at ₹15L") when they lead. The other side only when there is none."""
    before = [n for n in nums if n[2] <= span[0]]
    after = [n for n in nums if n[1] >= span[1]]
    first, second = (before[-1:], after[:1]) if follows else (after[:1], before[-1:])
    return (first or second or [None])[0]


def _measure_binds(header, value, measures, quote, nums, allowed) -> bool:
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
        if is_own and all(w in allowed for w in _LONG_WORD.findall(stretch)):
            return True
    return False


def _bind_group(columns: list[str], rows: list[list[str]], quote: str) -> list[bool]:
    """Which of these rows (all quoting ``quote``) bind cell to cell.

    Each figure binds to its own measure (``_measure_binds``), and each label
    to its own figure. Rows of one sentence follow one convention -- labels
    after their figures or before them -- so the convention that binds the
    most rows is the sentence's, and a row it does not bind is dropped (a tie
    keeps only the rows both readings bind). A
    label every row of the sentence shares ("In 2024, ...") is context, not
    a binding. A mismatch is dropped, never repaired.
    """
    label_cells = [
        [(h, c) for h, c in zip(columns, cells) if c.strip() and _is_label(h, c, quote)]
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
        measured.append(bool(values) and all(
            _measure_binds(h, v, measures, quote, nums, allowed) for h, v in values
        ))

    def binds(index: int, follows: bool) -> bool:
        for header, cell in label_cells[index]:
            if (header, _norm(cell)) in shared:
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


def verify_rows(
    document: str, rows: list[dict], columns: list[str] | None = None,
) -> list[tuple[list[str], str]]:
    """``(cells, quote)`` for each row whose quote is in the document, whose
    every cell is in its quote, and (with ``columns``) whose cells bind to
    each other (``_bind_group``).

    A mismatch is dropped, never repaired. ``columns`` is omitted only by tests
    of the quote check itself; extraction always passes them.
    """
    doc_norm = _norm(document)
    header = [str(c) for c in columns[:MAX_COLUMNS]] if columns else None
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
        if not all(_cell_in_quote(c, quote_norm, numbers) for c in cells):
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
        verdicts = _bind_group(header, [checked[i][0] for i in indexes], quote_norm)
        for i, ok in zip(indexes, verdicts):
            keep[i] = ok
    return [(cells, quote) for (cells, quote, _), ok in zip(checked, keep) if ok]


def build_prompt(title: str, text: str) -> str:
    """The prompt. The document is outside text: policy before, reminder after."""
    fenced = scrub_untrusted_text(text)
    return (
        "Find FIGURES written in the sentences of this document and return them "
        "as small tables a chart could be drawn from.\n\n"
        "Rules:\n"
        "- Only figures the text states. Never compute, total, convert or guess.\n"
        "- One table per kind of figure (for example revenue by quarter). Columns "
        "are short labels (Quarter, Region, Revenue). Cells are copied exactly as "
        "written in the text (\"₹15L\", \"Q2\", \"North\").\n"
        "- When several measures share the same periods (revenue, headcount and "
        "churn for Q1, Q2 and Q3), return ONE table with a column for the period, "
        "a column for the measure (the word in the sentence — revenue, headcount, "
        "churn — never Count or Value), and a column for the figure. One row per "
        "figure.\n"
        "- Every row MUST include \"quote\": the exact sentence it was read from, "
        "copied character for character. Every cell of the row must appear inside "
        "that quote, and the figure's measure must be in the same clause as the "
        "figure.\n"
        "- Skip figures that are not part of a comparable series (a single phone "
        "number, a page count, a date on its own).\n"
        "- If there are no such figures, return {\"tables\": []}.\n\n"
        "Reply with ONLY a JSON object:\n"
        '{"tables": [{"name": "<short title>", "columns": ["<label>", ...], '
        '"rows": [{"cells": ["<text>", ...], "quote": "<exact sentence>"}]}]}\n\n'
        f"{UNTRUSTED_POLICY}"
        "<<<UNTRUSTED_DOCUMENT>>>\n"
        f"Title: {scrub_untrusted_text(title)[:200]}\n\n{fenced}\n"
        "<<<END_UNTRUSTED_DOCUMENT>>>\n"
        f"{UNTRUSTED_REMINDER}"
    )


_JSON = re.compile(r"\{.*\}", re.S)


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
        truncated = len(prose) > self._max_chars
        prose = prose[: self._max_chars]
        llm = self._model()
        try:
            reply = llm.generate(build_prompt(doc.title, prose), max_tokens=MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001 - our type, the caller decides
            raise ProviderError("text adapter: model call failed", cause=exc) from exc
        if getattr(llm, "last_truncated", False):
            # A cut reply is not "this page has no figures": never marked read.
            raise ProviderError("text adapter: the reply was cut off before the table was finished")

        match = _JSON.search(reply or "")
        try:
            data = json.loads(match.group(0)) if match else {}
        except (ValueError, TypeError):
            data = {}
        found = data.get("tables") if isinstance(data, dict) else None
        if not isinstance(found, list):
            logger.info("text adapter: no usable reply for %s", doc.external_id)
            return []

        out: list[extract.Table] = []
        for item in found[:MAX_TABLES]:
            if not isinstance(item, dict):
                continue
            header = [str(c)[:80] for c in (item.get("columns") or [])][:MAX_COLUMNS]
            raw_rows = item.get("rows") or []
            rows = verify_rows(prose, raw_rows, header)
            if len(header) < extract.MIN_COLUMNS or len(rows) < extract.MIN_ROWS:
                continue
            notes = [TEXT_NOTE]
            claimed = sum(isinstance(r, dict) for r in raw_rows)
            if claimed > len(rows):
                notes.append(
                    f"{claimed - len(rows)} figure(s) were left out because the "
                    "number did not bind to its label or measure in its sentence."
                )
            if truncated:
                notes.append(f"Only the first {self._max_chars} characters were read.")
            raw = extract.RawTable(
                name=str(item.get("name") or doc.title)[:200],
                header=header,
                rows=[cells + [""] * (len(header) - len(cells)) for cells, _ in rows],
                notes=tuple(notes),
            )
            table = extract.profile(raw)
            if table is None or not any(c.type == "number" for c in table.columns):
                continue  # nothing to add up is not a chart
            table = _as_long(replace(table, quotes=tuple(q for _, q in rows)))
            out.append(table)
        return out


def _column_is_period(column: extract.Column) -> bool:
    samples = [str(sample) for sample in column.samples if str(sample).strip()]
    if not samples:
        return False
    hits = sum(1 for sample in samples if extract.PERIOD_TOKEN.match(_norm(sample).replace(" ", "")))
    return hits / len(samples) >= 0.5


def _as_long(table: extract.Table) -> extract.Table:
    """Several measures in one period row become period, measure, value.

    A grouped bar needs one number column and a series. The numbers were
    already checked against their own headers; this only reshapes them.
    """
    cols = list(table.columns)
    categories = [c for c in cols if c.type == "category"]
    numbers = [c for c in cols if c.type == "number"]
    if len(categories) != 1 or len(numbers) < 2 or not _column_is_period(categories[0]):
        return table
    period = categories[0]
    rows: list[list[str]] = []
    quotes: list[str | None] = []
    stored_quotes = list(table.quotes) + [None] * (len(table.cells) - len(table.quotes))
    for cells, raw, quote in zip(table.cells, table.raw, stored_quotes):
        index = cols.index(period)
        period_text = raw[index] if index < len(raw) else ""
        if not str(period_text).strip():
            continue
        for number in numbers:
            index = cols.index(number)
            if number.key not in cells:
                continue
            text = raw[index] if index < len(raw) else ""
            if not str(text).strip():
                continue
            rows.append([str(period_text), number.name, str(text)])
            quotes.append(quote)
    if len(rows) < extract.MIN_ROWS:
        return table
    melted = extract.profile(extract.RawTable(
        name=table.name, header=[period.name, "Measure", "Value"], rows=rows, notes=table.notes,
    ))
    if melted is None or not any(c.type == "number" for c in melted.columns):
        return table
    return replace(melted, quotes=tuple(quotes), notes=table.notes)


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
