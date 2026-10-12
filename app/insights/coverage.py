"""A chart is drawn only for measures the question actually asked for.

The model quotes the asker's words and names a column. This module keeps a
binding when the quote is in the question and the column is that measure
(the header's tokens, a close embedding, or a category value in a long
table). Anything else is named as missing. Nothing here repairs a binding
into a different column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN = re.compile(r"[a-z0-9]+")
_BRACKET = re.compile(r"\([^)]*\)")


def tokens(text: str) -> set[str]:
    """Alphanumeric tokens of a header or a quote, unit brackets removed."""
    bare = _BRACKET.sub(" ", text or "")
    return set(_TOKEN.findall(bare.lower()))


def format_binds(quote: str, header: str) -> bool:
    """True when every token of one side appears in the other, or one side
    abbreviates the other.

    A format check, not a word list: ``Revenue (₹ lakh)`` binds to
    "quarterly revenue"; ``Opex`` binds to "operating expenses"; ``Count``
    does not.
    """
    left, right = tokens(quote), tokens(header)
    if not left or not right:
        return False
    if left <= right or right <= left:
        return True
    return abbreviates(quote, header) or abbreviates(header, quote)


def _ordered_tokens(text: str) -> list[str]:
    return _TOKEN.findall(_BRACKET.sub(" ", text or "").lower())


def abbreviates(long: str, short: str) -> bool:
    """True when a token of ``short`` is made of the starts of at least two
    words of ``long``, in order: "opex" = OPerating EXpenses, "pl" = Profit
    (and) Loss. One word's start ("pro" of "profit") is not enough -- that is
    the "Pro" plan being read as profit."""
    words = _ordered_tokens(long)
    if len(words) < 2:
        return False
    parts = _ordered_tokens(short)
    # "P&L" is two one-letter tokens; read together it is the abbreviation.
    joined = ["".join(parts)] if 1 < len(parts) <= 4 and all(len(p) <= 2 for p in parts) else []
    for token in parts + joined:
        if len(token) >= 2 and _built_from(token, words, 0, 0):
            return True
    return False


def _built_from(token: str, words: list[str], at: int, used: int) -> bool:
    if not token:
        return used >= 2
    for i in range(at, len(words)):
        word = words[i]
        for size in range(min(len(word), len(token)), 0, -1):
            if token[:size] == word[:size] and _built_from(token[size:], words, i + 1, used + 1):
                return True
    return False


def squashed(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


@dataclass(frozen=True)
class MeasureAsk:
    """One measure the model says the asker named."""

    words: str
    handle: str | None = None
    column: str | None = None
    metric: str | None = None


@dataclass(frozen=True)
class Binding:
    """A measure that survived the evidence check."""

    words: str
    table_id: str | None
    column_key: str | None
    header: str
    unit: str
    metric: str | None = None
    filters: tuple[tuple[str, str], ...] = ()
    #: Wide table: one measure drawn as these number columns (the headers
    #: are the series). Empty when ``column_key`` is the single value.
    series_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoverageResult:
    bound: tuple[Binding, ...]
    missing: tuple[str, ...]


def cover(
    question: str,
    asks: list[MeasureAsk],
    handles: dict,
    *,
    similarity=None,
    bind_min: float = 0.60,
) -> CoverageResult:
    """Split ``asks`` into bound measures and the words that were not found.

    ``similarity(left, right) -> float`` is optional. When it is missing, only
    the format check and the category-value check apply.
    """
    asked = squashed(question)
    bound: list[Binding] = []
    missing: list[str] = []
    for ask in asks:
        words = (ask.words or "").strip()
        quote = squashed(words)
        if not quote or quote not in asked:
            if words:
                missing.append(words)
            continue
        binding = _bind_one(ask, handles, similarity=similarity, bind_min=bind_min)
        if binding is None:
            missing.append(_with_closest(words, handles, similarity))
        else:
            bound.append(binding)
    return CoverageResult(tuple(bound), tuple(missing))


#: A column this close is named as a hint beside a measure that did not bind.
#: Never drawn: below ``bind_min`` it is a suggestion for the asker, not data.
HINT_MIN = 0.55


def _with_closest(words: str, handles: dict, similarity) -> str:
    """"profit (closest column: Net income in \\"P&L\\")" -- so a refusal
    says what IS there instead of leaving the asker to guess the header."""
    if similarity is None:
        return words
    best, best_score = None, HINT_MIN
    for table in handles.values():
        for column in getattr(table, "columns", ()):
            if column.get("type") != "number":
                continue
            try:
                score = float(similarity(words, str(column.get("name") or "")))
            except (TypeError, ValueError):
                continue
            if score >= best_score:
                best, best_score = (column.get("name"), table), score
    if best is None:
        return words
    name, table = best
    return f"{words} (closest column: {name} in \"{getattr(table, 'document_title', '')}\")"


def _bind_one(ask: MeasureAsk, handles: dict, *, similarity, bind_min: float) -> Binding | None:
    if ask.metric and not ask.handle:
        label = (ask.column or ask.metric).strip()
        if not _evidence(ask.words, label, None, similarity=similarity, bind_min=bind_min):
            return None
        return Binding(
            words=ask.words.strip(), table_id=None, column_key=None,
            header=label, unit="", metric=ask.metric,
        )
    handle = (ask.handle or "").strip().upper()
    table = handles.get(handle)
    if table is None:
        return _only_wide(ask.words, handles)
    column_name = (ask.column or "").strip()
    column = _column_named(table, column_name) if column_name else None
    if column is not None and column.get("type") == "number" and _evidence(
        ask.words, column.get("name") or "", table, similarity=similarity, bind_min=bind_min,
    ):
        return Binding(
            words=ask.words.strip(), table_id=table.id, column_key=column["key"],
            header=column.get("name") or "", unit=column.get("unit") or "",
        )
    valued = _value_of_measure_column(
        ask.words, table, similarity=similarity, bind_min=bind_min,
    )
    if valued is not None:
        return valued
    wide = _wide_series(ask.words, table)
    if wide is not None:
        return wide
    return _only_wide(ask.words, handles)


def _only_wide(words: str, handles: dict) -> Binding | None:
    """One wide table whose headers the quote names. Two such tables bind nothing."""
    hits = [hit for table in handles.values() if (hit := _wide_series(words, table))]
    return hits[0] if len(hits) == 1 else None


def _column_named(table, name: str) -> dict | None:
    want = re.sub(r"[^a-z0-9]", "", name.lower())
    if not want:
        return None
    for column in table.columns:
        have = re.sub(r"[^a-z0-9]", "", str(column.get("name", "")).lower())
        if have == want:
            return column
    return None


def _evidence(quote: str, header: str, table, *, similarity, bind_min: float) -> bool:
    if format_binds(quote, header):
        return True
    if similarity is None:
        return False
    label = f"{header} ({getattr(table, 'name', '')})".strip()
    try:
        score = float(similarity(quote, label))
    except (TypeError, ValueError):
        return False
    return score >= bind_min


def _value_of_measure_column(quote: str, table, *, similarity, bind_min: float) -> Binding | None:
    """A long table (Measure | Value): the quote matches a category value."""
    numbers = [c for c in table.columns if c.get("type") == "number"]
    if len(numbers) != 1:
        return None
    for column in table.columns:
        if column.get("type") != "category":
            continue
        for sample in column.get("samples") or ():
            if not _evidence(quote, str(sample), table, similarity=similarity, bind_min=bind_min):
                continue
            number = numbers[0]
            return Binding(
                words=quote.strip(), table_id=table.id, column_key=number["key"],
                header=str(sample), unit=number.get("unit") or "",
                filters=((column["key"], str(sample)),),
            )
    return None


def _wide_series(quote: str, table) -> Binding | None:
    """``Quarter | Starter | Pro | Enterprise`` has no Plan column.

    The number headers are the series. Kept when at least two of those
    headers are the quote (or sit inside it).
    """
    numbers = [c for c in table.columns if c.get("type") == "number"]
    others = [c for c in table.columns if c.get("type") not in ("number", "text")]
    if len(others) != 1 or len(numbers) < 2:
        return None
    units = {(c.get("unit") or "") for c in numbers}
    if len(units) != 1 or "mixed" in units:
        return None
    named = [c for c in numbers if format_binds(quote, str(c.get("name") or ""))]
    if len(named) < 2:
        return None
    return Binding(
        words=quote.strip(), table_id=table.id, column_key=None,
        header=others[0].get("name") or "", unit=next(iter(units)),
        series_keys=tuple(c["key"] for c in named),
    )


@dataclass(frozen=True)
class ShapeChoice:
    shape: str
    note: str | None = None


def choose_shape(
    requested: str | None, *, units: list[str], n_measures: int, has_period: bool,
) -> ShapeChoice:
    """Admit a shape the data can draw. Refuses a stack or a dual axis the
    units cannot support. ``auto`` is chosen here, from the data."""
    distinct = list(dict.fromkeys(u or "" for u in units))
    asked = (requested or "auto").strip().lower()
    if asked in ("", "null", "none"):
        asked = "auto"
    if asked == "stacked_bar":
        if n_measures < 2:
            raise ValueError("A stacked chart needs at least two series of the same unit.")
        if len(set(distinct)) > 1:
            raise ValueError(
                "Those series use different units ("
                + ", ".join(u or "no unit" for u in distinct)
                + "), so they cannot be stacked."
            )
        return ShapeChoice("stacked_bar")
    if asked == "combo":
        if len(set(distinct)) > 2:
            raise ValueError(
                "A dual axis needs two units. These measures use "
                + ", ".join(u or "no unit" for u in distinct)
                + "."
            )
        if len(set(distinct)) < 2:
            return ShapeChoice(
                "grouped_bar",
                note="Both measures use the same unit, so they share one axis.",
            )
        return ShapeChoice("combo")
    if asked in ("pie", "donut"):
        if n_measures != 1:
            raise ValueError("A pie needs one measure and a category breakdown.")
        return ShapeChoice(asked)
    if asked == "auto":
        if len(set(distinct)) == 2 and n_measures >= 2:
            return ShapeChoice("combo")
        if has_period:
            return ShapeChoice("line" if n_measures <= 2 else "grouped_bar")
        if n_measures >= 2:
            return ShapeChoice("grouped_bar")
        return ShapeChoice("bar")
    if asked in ("line", "area", "bar", "grouped_bar", "horizontal_bar", "scatter"):
        return ShapeChoice(asked)
    return ShapeChoice("bar")


def not_available(missing: list[str], checked: list[str]) -> str:
    """Names what was asked and what was actually searched. No unrelated chart."""
    asked = ", ".join(m.strip() for m in missing if m and m.strip()) or "that"
    shown = ", ".join(f"\"{name}\"" for name in checked if name)
    where = f" I checked: {shown}." if shown else ""
    return (
        f"I couldn't find {asked} in your connected data.{where} "
        "Upload a sheet, or turn off Chart mode for a written answer."
    )
