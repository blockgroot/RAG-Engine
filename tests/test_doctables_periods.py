"""Period labels become a start, an end and a grain. No database."""

from datetime import date

import pytest

from app.doctables.periods import parse_period


@pytest.mark.parametrize("text,start,end,grain", [
    ("2026-04-01", date(2026, 4, 1), date(2026, 4, 1), "day"),
    ("Apr-25", date(2025, 4, 1), date(2025, 4, 30), "month"),
    ("April 2025", date(2025, 4, 1), date(2025, 4, 30), "month"),
    ("Q1 2026", date(2026, 1, 1), date(2026, 3, 31), "quarter"),
    ("H1 2026", date(2026, 1, 1), date(2026, 6, 30), "half"),
    ("2026", date(2026, 1, 1), date(2026, 12, 31), "year"),
])
def test_calendar_labels(text, start, end, grain):
    assert parse_period(text, 4) == (start, end, grain)


def test_fiscal_april_quarters_and_years():
    assert parse_period("Q1 FY26", 4) == (date(2025, 4, 1), date(2025, 6, 30), "quarter")
    assert parse_period("H2 FY26", 4) == (date(2025, 10, 1), date(2026, 3, 31), "half")
    assert parse_period("FY26", 4) == (date(2025, 4, 1), date(2026, 3, 31), "year")
    assert parse_period("FY 2025-26", 4) == (date(2025, 4, 1), date(2026, 3, 31), "year")
    assert parse_period("FY2025/26", 4) == (date(2025, 4, 1), date(2026, 3, 31), "year")


def test_january_fiscal_year_is_the_calendar_year():
    assert parse_period("Q1 FY26", 1) == (date(2026, 1, 1), date(2026, 3, 31), "quarter")
    assert parse_period("FY26", 1) == (date(2026, 1, 1), date(2026, 12, 31), "year")


def test_a_bare_quarter_inherits_one_year_from_the_heading():
    assert parse_period("Q1", 4, heading="Board update FY26") == (
        date(2025, 4, 1), date(2025, 6, 30), "quarter",
    )
    assert parse_period("Q1", 1, heading="Results 2026") == (
        date(2026, 1, 1), date(2026, 3, 31), "quarter",
    )


def test_a_bare_quarter_with_no_single_year_stays_unparsed():
    assert parse_period("Q1", 4) is None
    assert parse_period("Q1", 4, heading="Results 2024 and 2025") is None
    assert parse_period("not a period", 4) is None
