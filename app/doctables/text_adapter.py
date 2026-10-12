"""Figures written in SENTENCES, read by an AI and checked by code.

"Q1 revenue was ₹12L, Q2 grew to ₹15L" is data nobody put in a table, and no
parser can turn prose into rows: knowing that ₹15L is Q2's revenue is reading
comprehension. So an AI reads it -- in the BACKGROUND (``background = True``;
``queue.py`` runs it on the tick, never inside ingestion) -- and returns small
tables where every row carries the exact sentence it came from.

The AI is not trusted with the result. Code keeps a row only when:

- its quote appears in the document (whitespace- and case-insensitive),
- EVERY cell appears in that quote: a number must equal a number written in
  the quote (₹15L == 1,500,000), a label must occur in it verbatim, and
- each number sits in one clause with its own measure. A period named earlier
  in the same sentence still counts. A number nearer a different measure
  ("150" of headcount written under Revenue) is dropped, never moved.

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
#: How many unit-bearing figures a page needs before it is worth a model
#: call. A bare digit does not count: dates, clause numbers and "15 days"
#: are why a policy was taking a slot ahead of a budget page. A currency,
#: a percent, or a magnitude (lakh, k, crore, Mio) does.
MIN_FIGURES = 3

TEXT_NOTE = (
    "Figures read from sentences in this document by AI. Each value was checked "
    "against the sentence it came from (shown on hover); a figure the AI missed "
    "is not counted."
)

#: A figure worth charting: a currency, a percent, or a magnitude suffix.
#: "5min" and "3kg" do not match — the suffix has to end the token.
_CHART_FIGURE = re.compile(
    r"(?:[₹$€£]|rs\.?|inr|usd|eur|gbp)\s*\d"
    r"|\d[\d,]*(?:\.\d+)?\s*(?:k|mn|m|bn|b|lakhs?|lac|l|crores?|cr|mio)\b"
    r"|\d[\d,]*(?:\.\d+)?\s*%",
    re.I,
)
#: Anything a figure can be written as, for comparing a cell to its quote.
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


#: Headers that name no measure. "Count" matched inside "headcount" and became
#: a chart of a number that belonged to something else.
_GENERIC = frozenset({
    "value", "values", "count", "counts", "amount", "amounts", "total", "totals",
    "number", "numbers", "figure", "figures", "metric", "metrics",
})
_UNIT_WORDS = frozenset({
    "lakh", "lakhs", "lac", "crore", "crores", "cr", "million", "billion",
    "thousand", "percent", "percentage",
})
_BREAK = re.compile(r"[.!?;\n]+|\b(?:while|whereas|but|and)\b|,", re.I)


def _header_words(header: str) -> list[str]:
    name, _unit = split_header(header or "")
    return [
        w for w in _norm(name).split()
        if len(w) >= 3 and w not in _UNIT_WORDS and w not in _GENERIC
    ]


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


def _period_cell(cell: str) -> bool:
    return extract.PERIOD_TOKEN.match(_norm(cell).replace(" ", "")) is not None


def _label_spans(label: str, text: str) -> list[tuple[int, int]]:
    needle = _norm(label)
    if not needle:
        return []
    if re.fullmatch(r"[a-z0-9]+", needle):
        pattern = rf"\b{re.escape(needle)}\b"
    else:
        pattern = re.escape(needle)
    return [(m.start(), m.end()) for m in re.finditer(pattern, text)]


def _dist(left: tuple[int, int], right: tuple[int, int]) -> int:
    if left[0] <= right[1] and right[0] <= left[1]:
        return 0
    return min(abs(left[0] - right[1]), abs(right[0] - left[1]))


def _num_spans(text: str) -> list[tuple[float, int, int]]:
    out = []
    for match in _NUMBER_TOKEN.finditer(text):
        value, _unit = extract.parse_number(match.group(0).strip())
        if value is not None:
            out.append((value, match.start(), match.end()))
    return out


def _clause_spans(quote: str) -> list[tuple[str, int, int]]:
    """Pieces of a sentence that each carry their own figure.

    "revenue was ₹40 lakh while headcount reached 150" is two clauses.
    "Headcount moved from 140 in January to 150 in June" stays one: nothing
    splits it, and the month beside each number is what binds it.
    """
    parts: list[tuple[str, int, int]] = []
    last = 0
    for match in _BREAK.finditer(quote):
        parts.append((quote[last:match.start()], last, match.start()))
        last = match.end()
    parts.append((quote[last:], last, len(quote)))
    parts = [(text.strip(), start, end) for text, start, end in parts if text.strip()]
    if not parts:
        return [(quote, 0, len(quote))]
    merged = [parts[0]]
    for text, start, end in parts[1:]:
        prev, prev_start, _prev_end = merged[-1]
        if _numbers_in(prev) and _numbers_in(text):
            merged.append((text, start, end))
        else:
            merged[-1] = (quote[prev_start:end].strip(), prev_start, end)
    return merged


def _number_binds(
    header: str, cell: str, value: float, columns: list[str], cells: list[str], quote: str,
) -> bool:
    """This number's clause names its measure, and every other label either
    sits beside it or is a period named earlier in the same sentence."""
    specific = bool(_header_words(header))
    for clause, start, _end in _clause_spans(quote):
        spans = _num_spans(clause)
        matching = [(s, e) for n, s, e in spans if _close(n, value)]
        if not matching:
            continue
        others = [(s, e) for n, s, e in spans if not _close(n, value)]
        if specific and not _header_spans(header, clause):
            continue
        labels = _other_labels(columns, cells, cell, clause)
        if not specific and not any(len(_norm(label)) >= 4 for label in labels):
            continue
        if not _nearest_header(header, columns, matching, clause):
            continue
        if all(_label_binds(label, clause, start, quote, matching, others) for label in labels):
            return True
    return False


def _other_labels(columns: list[str], cells: list[str], cell: str, clause: str) -> list[str]:
    labels = []
    for other_header, other in zip(columns, cells):
        other = (other or "").strip()
        if not other or other == cell:
            continue
        value, _unit = extract.parse_number(other)
        if value is not None and not (_is_year(value) and not _header_spans(other_header, clause)):
            continue
        labels.append(other)
    return labels


def _nearest_header(
    header: str, columns: list[str], matching: list[tuple[int, int]], clause: str,
) -> bool:
    """A different column's measure sitting closer to the number means the
    number is that measure's, so this column does not get it."""
    own = _header_spans(header, clause)
    if not own:
        return True
    rivals = []
    for other in columns:
        if other == header:
            continue
        rivals.extend(_header_spans(other, clause))
    if not rivals:
        return True
    for span in matching:
        own_dist = min(_dist(span, item) for item in own)
        if all(own_dist <= _dist(span, rival) for rival in rivals):
            return True
    return False


def _closest_number(label: tuple[int, int], spans: list[tuple[int, int]]) -> tuple[int, int]:
    """The number this label belongs to. A tie goes to the number before the label
    ("140 in January", not the 150 that follows it)."""
    best = spans[0]
    best_key = None
    for span in spans:
        key = (_dist(label, span), 0 if span[1] <= label[0] else 1)
        if best_key is None or key < best_key:
            best_key, best = key, span
    return best


def _label_binds(
    label: str, clause: str, clause_start: int, quote: str,
    matching: list[tuple[int, int]], others: list[tuple[int, int]],
) -> bool:
    inside = _label_spans(label, clause)
    if inside:
        if not others:
            return True
        pool = matching + others
        return any(_closest_number(label_span, pool) in matching for label_span in inside)
    if _period_cell(label) and any(end <= clause_start for _start, end in _label_spans(label, quote)):
        return True
    return False


def _row_binds(columns: list[str], cells: list[str], quote: str) -> bool:
    saw = False
    for header, cell in zip(columns, cells):
        cell = (cell or "").strip()
        if not cell:
            continue
        value, _unit = extract.parse_number(cell)
        if value is None or (_is_year(value) and not _header_spans(header, quote)):
            continue
        saw = True
        if not _number_binds(header, cell, value, columns, cells, quote):
            return False
    return saw


def verify_rows(
    document: str, rows: list[dict], columns: list[str] | None = None,
) -> list[tuple[list[str], str]]:
    """``(cells, quote)`` for each row whose quote is in the document and whose
    every cell is in its quote.

    With ``columns``, each number must also sit in one clause with its measure.
    A mismatch is dropped, never repaired. ``columns`` is omitted only by tests
    of the quote check itself; extraction always passes them.
    """
    doc_norm = _norm(document)
    kept = []
    header = [str(c) for c in columns[:MAX_COLUMNS]] if columns else None
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
        if header is not None:
            width = header[:len(cells)] if len(header) >= len(cells) else None
            if width is None or not _row_binds(width, cells, quote_norm):
                continue
        kept.append((cells, quote.strip()))
    return kept


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
    background = True

    def __init__(self, llm=None, *, max_chars: int = 12000):
        self._llm = llm
        self._max_chars = max_chars

    def wants(self, doc: DocumentText) -> bool:
        """Cheap: at least ``MIN_FIGURES`` unit-bearing figures in the prose.

        Dates, clause numbers and day counts are not a series, so a policy
        page does not take a queue slot. The AI's rows are still checked
        against their sentences."""
        if doc.tables:
            # A Sheet or CSV: its rows ARE the data, read exactly already.
            return False
        prose = _prose(doc.content)
        found = 0
        for _ in _CHART_FIGURE.finditer(prose):
            found += 1
            if found >= MIN_FIGURES:
                return True
        return False

    def extract(self, doc: DocumentText) -> list[extract.Table]:
        if self._llm is None:
            raise ProviderError("text adapter: no model configured")
        prose = _prose(doc.content)
        truncated = len(prose) > self._max_chars
        prose = prose[: self._max_chars]
        try:
            reply = self._llm.generate(build_prompt(doc.title, prose), max_tokens=MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001 - retried by the queue
            raise ProviderError("text adapter: model call failed", cause=exc) from exc
        if getattr(self._llm, "last_truncated", False):
            # A cut reply is not "this page has no figures". The queue retries.
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
                    "number did not sit with its measure in the same sentence."
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
    providers: list[str] | None, llm,
) -> ProseRead:
    """Read figures from at most two pages whose text matches the question.

    The library sweep stays off (``DOCTABLES_TEXT_ENABLED``). This runs because
    someone is waiting in Chart mode and nothing stored answers them. Never
    raises: a failed read costs the chart, not the refusal.
    """
    from ..config.settings import DocTablesSettings

    from .queue import mark_read
    from .store import matching_prose, replace_document_tables

    if llm is None or viewer is None or getattr(viewer, "is_unrestricted", False):
        return ProseRead((), ())
    try:
        pages = matching_prose(
            org_id=org_id, workspace_id=workspace_id, viewer=viewer,
            question=question, providers=providers,
        )
    except Exception:  # noqa: BLE001
        logger.warning("text adapter: could not find pages to read", exc_info=True)
        return ProseRead((), ())
    adapter = TextAdapter(llm, max_chars=DocTablesSettings.from_env().text_max_chars)
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
