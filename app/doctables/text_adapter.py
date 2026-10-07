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
#: A document needs at least this many figure-like tokens to be worth a call.
MIN_FIGURES = 3

TEXT_NOTE = (
    "Figures read from sentences in this document by AI. Each value was checked "
    "against the sentence it came from (shown on hover); a figure the AI missed "
    "is not counted."
)

_FIGURE = re.compile(
    r"(?:[₹$€£]|\brs\.?|\binr\b|\busd\b|\beur\b)\s*\d"
    # The unit may touch the number ("12k", "3.2bn"): no word boundary BEFORE
    # it, only after, so "5min" or "3kg" is still not a figure.
    r"|\d[\d,]*(?:\.\d+)?\s*(?:%|(?:k|mn?|bn|lakhs?|lac|cr|crores?)\b)",
    re.I,
)
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


def verify_rows(document: str, rows: list[dict]) -> list[tuple[list[str], str]]:
    """``(cells, quote)`` for each row whose quote is in the document and whose
    every cell is in its quote. Everything else is dropped -- never repaired."""
    doc_norm = _norm(document)
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
        if all(_cell_in_quote(c, quote_norm, numbers) for c in cells):
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
        "- Every row MUST include \"quote\": the exact sentence (or clause) it was "
        "read from, copied character for character. Every cell of the row must "
        "appear inside that quote.\n"
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
        """Cheap: at least ``MIN_FIGURES`` money/percent/scaled figures in the
        prose. Most documents (policies, notes) have none and never cost a call."""
        if doc.tables:
            # A Sheet or CSV: its rows ARE the data, read exactly already.
            return False
        prose = _prose(doc.content)
        found = 0
        for _ in _FIGURE.finditer(prose):
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
            rows = verify_rows(prose, item.get("rows") or [])
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
