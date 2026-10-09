"""What a chart's numbers MEAN, in words, for every chart: title, unit, and a
one-line explanation of what each bar, point or slice is.

"130 Salary (₹ lakh)" was a correct number nobody could read: the unit was
the column's whole header, and nothing said a bar was a SUM of that column
over a team's rows. Everything here is built from what the chart already
knows -- the measure that ran, the column's own header and parsed unit, the
breakdown's label, the period -- never from a list of words, so a new column
or a new metric reads correctly without anyone writing copy for it.
"""

from __future__ import annotations

import re

#: "Salary (₹ lakh)" -> ("Salary", "₹ lakh"). A trailing bracket in a header
#: is where people write the unit; this is format parsing, not intent.
_BRACKETED = re.compile(r"^(?P<name>.*?)\s*[\(\[](?P<unit>[^\)\]]{1,24})[\)\]]\s*$")

#: A unit that starts with a symbol (₹, $, €, %) is written against the
#: number: "₹130 lakh", "12%". Decided by the character class, not a list.
_SYMBOL = re.compile(r"^[^\w\s(]+", re.UNICODE)

#: How a table measure reads in a title and in the explanation.
_TABLE_TITLE = {"sum": "Total", "average": "Average", "min": "Lowest", "max": "Highest"}
_TABLE_VERB = {"sum": "adds up", "average": "is the average", "min": "is the lowest",
               "max": "is the highest"}


def split_header(header: str) -> tuple[str, str]:
    """``(name, unit)`` from a column header; unit is "" when none is written."""
    found = _BRACKETED.match(header or "")
    if not found or not found.group("name").strip():
        return (header or "").strip(), ""
    return found.group("name").strip(), found.group("unit").strip()


def format_value(value: float, unit: str) -> str:
    """A number with its unit, the way a person writes it: "₹130 lakh",
    "12%", "4 tasks". Mirrors ``withUnit`` in the frontend."""
    shown = f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0")
    unit = (unit or "").strip()
    if not unit:
        return shown
    symbol = _SYMBOL.match(unit)
    if symbol:
        rest = unit[symbol.end():].strip()
        if not rest and len(symbol.group(0)) == 1 and unit == "%":
            return f"{shown}%"
        return f"{symbol.group(0)}{shown}" + (f" {rest}" if rest else "")
    singular = unit[:-1] if value == 1 and unit.endswith("s") else unit
    return f"{shown} {singular}"


def _mark(chart: str | None) -> str:
    return {"line": "point", "pie": "slice"}.get(chart or "", "bar")


def _where(group: str | None, split: str | None, period: str, *, by_date: bool) -> str:
    if by_date:
        return f"in each {period}" + (f", split by {split}" if split else "")
    if group and split:
        return f"for each {group} and {split}"
    if group:
        return f"for each {group}"
    return f"in each {period}"


# ---------------------------------------------------------------------------
# Tables inside documents and uploads
# ---------------------------------------------------------------------------


def table_unit(measure: str, value_col: dict | None) -> str:
    """The unit of one bar: the header's bracketed unit, else the symbol the
    cells were written with, else none. A count is rows."""
    if measure == "count" or not value_col:
        return "rows"
    _, bracketed = split_header(value_col.get("name", ""))
    return bracketed or (value_col.get("unit") or "")


def table_title(measure: str, value_col: dict | None, group: str | None,
                split: str | None) -> str:
    """"Total Salary by Team" -- the measure, the column without its unit
    (the unit is on every value), and the breakdown."""
    if measure == "count" or not value_col:
        title = "Number of rows"
    else:
        name, _ = split_header(value_col.get("name", ""))
        title = f"{_TABLE_TITLE.get(measure, measure.title())} {name}"
    if group:
        title += f" by {split_header(group)[0]}"
        if split:
            title += f" and {split_header(split)[0]}"
    return title


def table_explain(measure: str, value_col: dict | None, *, group: str | None,
                  split: str | None, period: str, by_date: bool, chart: str | None,
                  rows: int, total: float | None, unit: str) -> str:
    """"Each bar adds up Salary for the rows of each Team. 10 rows, ₹266 lakh
    in total." -- what was done to which column, over which rows."""
    mark = _mark(chart)
    group_name = split_header(group)[0] if group else None
    split_name = split_header(split)[0] if split else None
    where = (f"for the rows dated in each {period}" if by_date
             else f"for the rows of each {group_name}" if group_name
             else "across all rows")
    if split_name:
        where += f", split by {split_name}"
    if measure == "count" or not value_col:
        what = f"Each {mark} counts the table's rows {where.replace('for the rows ', '')}"
    else:
        column = split_header(value_col.get("name", ""))[0]
        what = f"Each {mark} {_TABLE_VERB.get(measure, measure)} {column} {where}"
    tail = f" {rows:,} rows"
    if total is not None and measure == "sum":
        tail += f", {format_value(total, unit)} in total"
    return f"{what}.{tail}."


# ---------------------------------------------------------------------------
# Activity from the connected tools
# ---------------------------------------------------------------------------


def _is_spread(chosen) -> bool:
    """An average/median/longest of a value (its unit carries the bracket,
    "days (median)") rather than a count or a total."""
    _, how = split_header(chosen.unit)
    return bool(how) and (chosen.label is None or chosen.label.lower() == how.lower())


def activity_measure(metric, chosen) -> str | None:
    """What a NON-default measure is, for the title: "number of different
    people", "average lines added", "longest, in days". None for the
    metric's own measure, which the metric's label already names."""
    if chosen is None or chosen.select is None:
        return None
    if chosen.name in ("people", "subjects"):
        return f"number of different {chosen.unit}"
    if _is_spread(chosen):
        base, how = split_header(chosen.unit)
        return f"{how.lower()} (in {base})"
    return (chosen.label or chosen.name.replace("_", " ")).lower()


def activity_explain(metric, chosen, *, group: str | None, split: str | None,
                     period: str, chart: str | None) -> str:
    """"Each point shows how many tasks in each week." Built from the measure
    that ran, the metric's own unit and label, and the breakdown -- the same
    rule for every connector."""
    if chosen is None:
        return ""
    if chosen.name in ("people", "subjects"):
        what = f"how many different {chosen.unit}"
    elif _is_spread(chosen):
        base, how = split_header(chosen.unit)
        what = f"the {how.lower()} {metric.label.lower()} (in {base})"
    elif chosen.select is None:
        what = f"how many {chosen.unit}"
    else:
        what = f"the {(chosen.label or chosen.name).lower()}"
    return (f"Each {_mark(chart)} shows {what} "
            f"{_where(group, split, period, by_date=False)}.")
