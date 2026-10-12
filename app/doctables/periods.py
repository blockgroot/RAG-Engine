"""Period labels become a start date, an end date and a grain.

Format parsing only (CLAUDE.md): a quarter, a half, a fiscal year, a month
or a date is a pattern, not a guess about what the asker meant. A bare
``Q1`` inherits a year only when the heading states exactly one year or one
fiscal year. Two years, or none, stay unparsed.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def parse_period(
    text: str, fiscal_start_month: int = 1, *, heading: str | None = None,
) -> tuple[date, date, str] | None:
    """``(start, end, grain)`` or None. ``grain`` is day, month, quarter, half or year.

    ``fiscal_start_month`` is the org's fiscal year start (1 = January,
    4 = April). It changes ``FY`` labels only. ``Q1 2026`` with no FY is
    the calendar quarter either way.
    """
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return None
    month = _clamp_month(fiscal_start_month)
    parsed = (
        _iso_day(raw)
        or _month_label(raw)
        or _quarter(raw, month, heading)
        or _half(raw, month, heading)
        or _fiscal_year(raw, month)
        or _calendar_year(raw)
    )
    return parsed


def _clamp_month(month: int) -> int:
    return month if 1 <= month <= 12 else 1


def _end_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _add_months(year: int, month: int, count: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + count
    return index // 12, index % 12 + 1


def _span(year: int, month: int, months: int) -> tuple[date, date]:
    end_y, end_m = _add_months(year, month, months - 1)
    return date(year, month, 1), _end_of_month(end_y, end_m)


def _year_token(token: str) -> int | None:
    if re.fullmatch(r"(19|20)\d{2}", token):
        return int(token)
    if re.fullmatch(r"\d{2}", token):
        value = int(token)
        return 2000 + value if value < 70 else 1900 + value
    return None


def _fy_bounds(label_year: int, fiscal_start: int) -> tuple[date, date]:
    """``FY26`` is the fiscal year that *ends* in that year when the year
    does not start in January. January-start fiscal years are the calendar year."""
    if fiscal_start == 1:
        return date(label_year, 1, 1), date(label_year, 12, 31)
    start = date(label_year - 1, fiscal_start, 1)
    end = date(label_year, fiscal_start, 1) - timedelta(days=1)
    return start, end


def _iso_day(text: str) -> tuple[date, date, str] | None:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if not match:
        return None
    try:
        day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return day, day, "day"


def _month_label(text: str) -> tuple[date, date, str] | None:
    match = re.fullmatch(
        r"([A-Za-z]{3,9})[ .\-/](\d{2,4})", text,
    )
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    year = _year_token(match.group(2))
    if month is None or year is None:
        return None
    return date(year, month, 1), _end_of_month(year, month), "month"


def _heading_context(heading: str | None, fiscal_start: int) -> tuple[date, date, str] | None:
    """The single year or fiscal year written in a title, if there is exactly one."""
    text = heading or ""
    fiscal = re.findall(r"\bFY\s*(\d{2,4})\b", text, flags=re.I)
    years = re.findall(r"\b(?:19|20)\d{2}\b", text)
    spans = re.findall(r"\bFY\s*((?:19|20)\d{2})\s*[-/]\s*(\d{2,4})\b", text, flags=re.I)
    if len(spans) == 1 and not fiscal:
        end = _year_token(spans[0][1])
        if end is None:
            return None
        start, stop = _fy_bounds(end, fiscal_start)
        return start, stop, "year"
    if len(fiscal) == 1 and len(years) <= 1:
        label = _year_token(fiscal[0])
        if label is None:
            return None
        start, stop = _fy_bounds(label, fiscal_start)
        return start, stop, "year"
    if fiscal or spans or len(years) != 1:
        return None
    year = int(years[0])
    return date(year, 1, 1), date(year, 12, 31), "year"


def _quarter(text: str, fiscal_start: int, heading: str | None) -> tuple[date, date, str] | None:
    match = re.fullmatch(
        r"Q([1-4])(?:\s+FY\s*(\d{2,4})|\s+((?:19|20)\d{2}))?",
        text, flags=re.I,
    )
    if not match:
        return None
    which = int(match.group(1))
    if match.group(2):
        label = _year_token(match.group(2))
        if label is None:
            return None
        start, _end = _fy_bounds(label, fiscal_start)
        fy = True
    elif match.group(3):
        start = date(int(match.group(3)), 1, 1)
        fy = False
    else:
        ctx = _heading_context(heading, fiscal_start)
        if ctx is None:
            return None
        start = ctx[0]
        fy = fiscal_start != 1 and bool(re.search(r"FY", heading or "", flags=re.I))
        if not fy:
            start = date(start.year, 1, 1)
    if fy:
        year, month = _add_months(start.year, start.month, (which - 1) * 3)
    else:
        year, month = start.year, (which - 1) * 3 + 1
    begin, end = _span(year, month, 3)
    return begin, end, "quarter"


def _half(text: str, fiscal_start: int, heading: str | None) -> tuple[date, date, str] | None:
    match = re.fullmatch(
        r"H([12])(?:\s+FY\s*(\d{2,4})|\s+((?:19|20)\d{2}))?",
        text, flags=re.I,
    )
    if not match:
        return None
    which = int(match.group(1))
    if match.group(2):
        label = _year_token(match.group(2))
        if label is None:
            return None
        start, _end = _fy_bounds(label, fiscal_start)
    elif match.group(3):
        start = date(int(match.group(3)), 1, 1)
    else:
        ctx = _heading_context(heading, fiscal_start)
        if ctx is None:
            return None
        start = ctx[0] if re.search(r"FY", heading or "", flags=re.I) else date(ctx[0].year, 1, 1)
    year, month = _add_months(start.year, start.month, 0 if which == 1 else 6)
    begin, end = _span(year, month, 6)
    return begin, end, "half"


def _fiscal_year(text: str, fiscal_start: int) -> tuple[date, date, str] | None:
    span = re.fullmatch(
        r"FY\s*((?:19|20)\d{2})\s*[-/]\s*(\d{2,4})", text, flags=re.I,
    )
    if span:
        end = _year_token(span.group(2))
        if end is None:
            return None
        start, stop = _fy_bounds(end, fiscal_start)
        return start, stop, "year"
    one = re.fullmatch(r"FY\s*(\d{2,4})", text, flags=re.I)
    if not one:
        return None
    label = _year_token(one.group(1))
    if label is None:
        return None
    start, stop = _fy_bounds(label, fiscal_start)
    return start, stop, "year"


def _calendar_year(text: str) -> tuple[date, date, str] | None:
    if not re.fullmatch(r"(19|20)\d{2}", text):
        return None
    year = int(text)
    return date(year, 1, 1), date(year, 12, 31), "year"
