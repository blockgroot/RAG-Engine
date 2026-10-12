"""Figures written in SENTENCES, read by an AI and checked by code.

"Q1 revenue was ₹12L, Q2 grew to ₹15L" is data nobody put in a table, and no
parser can turn prose into rows: knowing that ₹15L is Q2's revenue is reading
comprehension. So an AI reads it -- in the BACKGROUND (``background = True``;
``queue.py`` runs it on the tick, never inside ingestion) -- and returns small
tables where every row carries the exact sentence it came from.

The AI is not trusted with the result. Code keeps a row only when:

- its quote appears in the document (whitespace- and case-insensitive), and
- EVERY cell appears in that quote: a number must equal a number written in
  the quote (₹15L == 1,500,000), a label must occur in it verbatim.

So an invented figure, a changed figure, or a figure moved onto the wrong
label ("Q3" on a sentence about Q2) fails the check and is dropped. What the
check cannot catch is a figure the AI MISSED -- the chart says it was read
from text, and the hover shows each sentence, so a reader can judge.

Bounded: ``max_chars`` of the document (pipe tables removed -- the table
adapter already has those, exactly), at most ``MAX_TABLES`` tables of
``MAX_ROWS`` rows, and one model call per document.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import replace

from ..core.exceptions import ProviderError
from ..security.untrusted import UNTRUSTED_POLICY, UNTRUSTED_REMINDER, scrub_untrusted_text
from . import extract
from .base import DatasetAdapter, DocumentText

logger = logging.getLogger(__name__)

ORIGIN = "text"
MAX_TABLES = 5
MAX_ROWS = 200
MAX_COLUMNS = 8
MAX_TOKENS = 1500
#: A document needs at least this many numbers in its prose to be worth a
#: call. A COUNT, not a list of currencies or units: "₹12L", "12k", "40 %",
#: "1.2 Mio." and a figure in any script all count the same, and a policy page
#: with no figures never costs a call.
MIN_NUMBERS = 5

TEXT_NOTE = (
    "Figures read from sentences in this document by AI. Each value was checked "
    "against the sentence it came from (shown on hover); a figure the AI missed "
    "is not counted."
)

_A_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
#: Anything a figure can be written as, for comparing a cell to its quote.
_NUMBER_TOKEN = re.compile(
    r"\(?-?(?:[₹$€£]|rs\.?|inr|usd|eur|gbp)?\s*\d[\d,\s]*(?:\.\d+)?\s*"
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


def _cell_in_quote(cell: str, quote_norm: str, quote_numbers: list[float]) -> bool:
    cell = (cell or "").strip()
    if not cell:
        return True  # an empty cell claims nothing
    value, _ = extract.parse_number(cell)
    if value is not None:
        return any(abs(value - n) <= 1e-6 * max(1.0, abs(n)) for n in quote_numbers)
    return _norm(cell) in quote_norm


#: How far before a clause its context (a heading, a list's lead-in) may end.
CONTEXT_WINDOW = 400


def _context_ok(context: str, quote_norm: str, doc_norm: str) -> bool:
    """The context is in the document and sits just before the clause (or
    contains it). Otherwise a label could be borrowed from anywhere."""
    ctx = _norm(context)
    if not ctx or ctx not in doc_norm:
        return False
    if quote_norm in ctx:
        return True
    at = doc_norm.find(quote_norm)
    while at != -1:
        start = doc_norm.rfind(ctx, max(0, at - CONTEXT_WINDOW - len(ctx)), at)
        if start != -1 and at - (start + len(ctx)) <= CONTEXT_WINDOW:
            return True
        at = doc_norm.find(quote_norm, at + 1)
    return False


def _values_left(quote: str, labels: list[str]) -> list[float]:
    """Numbers in the clause once the row's labels are taken out: "Q2 2026"
    names a period, it is not a figure."""
    text = _norm(quote)
    for label in sorted((_norm(l) for l in labels if l), key=len, reverse=True):
        text = text.replace(label, " ")
    return _numbers_in(text)


def verify_rows(document: str, rows: list[dict],
                header: list[str] | None = None) -> list[tuple[list[str], str]]:
    """``(cells, quote)`` for each row that passes every check. Everything else
    is dropped -- never repaired.

    1. The clause is in the document, and every cell is in the clause.
    2. ONE figure per row, and the clause holds exactly that one figure once
       the row's labels are removed. "Q1 revenue was ₹12L, Q2 grew to ₹15L"
       holds two, so a row quoting it for Q2 is ambiguous and is dropped:
       any-number matching used to accept ``["Q2", "₹12L"]``.
    3. The figure's column is named: its header is in the clause or in the
       row's ``context`` (verbatim, ending at most ``CONTEXT_WINDOW`` before
       the clause), or the row carries the measure as a label of its own
       (a "Metric | Value" table). A revenue figure filed under "Expenses"
       fails here.
    """
    from ..insights.coverage import format_binds

    doc_norm = _norm(document)
    header = header or []
    kept = []
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
        figures = [i for i, c in enumerate(cells)
                   if c.strip() and extract.parse_number(c)[0] is not None]
        labels = [c for i, c in enumerate(cells) if i not in figures and c.strip()]
        if len(figures) != 1 or len(_values_left(quote, labels)) != 1:
            continue
        context = row.get("context") if isinstance(row.get("context"), str) else ""
        named_in = quote + (" " + context if context and _context_ok(context, quote_norm, doc_norm) else "")
        name = header[figures[0]] if figures[0] < len(header) else ""
        if header and (not name or not format_binds(name, named_in)):
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
        "- One table per MEASURE (for example revenue by quarter; operating "
        "expenses by quarter is another table). Columns are short labels: the "
        "labels first (Quarter, Region, Plan), then ONE number column named with "
        "the measure's words as the text writes them (Revenue, Churn rate) -- "
        "never \"Value\" or \"Amount\". Cells are copied exactly as written "
        "(\"₹15L\", \"Q2 FY26\", \"North\").\n"
        "- Every row MUST include \"quote\": the SHORTEST clause holding that "
        "row's one figure, copied character for character, with no other figure "
        "in it. Every cell of the row must appear inside that quote. When the "
        "measure's name or the period is written just before the clause (a "
        "heading, \"Revenue by quarter:\", the start of the sentence), copy that "
        "text into \"context\".\n"
        "- Skip figures that are not part of a comparable series (a single phone "
        "number, a page count, a date on its own).\n"
        "- If there are no such figures, return {\"tables\": []}.\n\n"
        "Reply with ONLY a JSON object:\n"
        '{"tables": [{"name": "<short title>", "columns": ["<label>", ...], '
        '"rows": [{"cells": ["<text>", ...], "quote": "<exact clause>", '
        '"context": "<exact text just before it, or null>"}]}]}\n\n'
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
        """Cheap: at least ``MIN_NUMBERS`` numbers in the prose, in any format
        or script. Most documents (policies, notes) have few and never cost a
        call; the AI's rows are checked against their sentences either way."""
        if doc.tables:
            # A Sheet or CSV: its rows ARE the data, read exactly already.
            return False
        prose = _prose(doc.content)
        found = 0
        for _ in _A_NUMBER.finditer(prose):
            found += 1
            if found >= MIN_NUMBERS:
                return True
        return False

    def extract(self, doc: DocumentText) -> list[extract.Table]:
        if self._llm is None:
            raise ProviderError("text adapter: no model configured")
        prose = _prose(doc.content)
        truncated = len(prose) > self._max_chars
        prose = prose[: self._max_chars]
        try:
            from ..llm.base import call_generate

            reply = call_generate(
                self._llm, build_prompt(doc.title, prose),
                max_tokens=MAX_TOKENS, temperature=0, json_mode=True,
            )
        except Exception as exc:  # noqa: BLE001 - retried by the queue
            raise ProviderError("text adapter: model call failed", cause=exc) from exc

        # A reply that is not the JSON asked for (cut off at the token cap, or
        # prose) is a FAILURE to retry, never "this document has no figures":
        # that used to mark the document done and its figures were never read.
        match = _JSON.search(reply or "")
        try:
            data = json.loads(match.group(0)) if match else None
        except (ValueError, TypeError) as exc:
            raise ProviderError("text adapter: reply was not JSON", cause=exc) from exc
        found = data.get("tables") if isinstance(data, dict) else None
        if not isinstance(found, list):
            raise ProviderError(f"text adapter: no usable reply for {doc.external_id}")

        out: list[extract.Table] = []
        for item in found[:MAX_TABLES]:
            if not isinstance(item, dict):
                continue
            header = [str(c)[:80] for c in (item.get("columns") or [])][:MAX_COLUMNS]
            rows = verify_rows(prose, item.get("rows") or [], header)
            if len(header) < extract.MIN_COLUMNS or len(rows) < extract.MIN_ROWS:
                continue
            notes = [TEXT_NOTE]
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
            out.append(replace(table, quotes=tuple(q for _, q in rows)))
        return out
