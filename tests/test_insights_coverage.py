"""A chart is drawn only for the measure the question asked for."""

from types import SimpleNamespace

import pytest

from app.insights.coverage import (
    MeasureAsk, choose_shape, cover, format_binds, not_available,
)


def _table(**kwargs):
    columns = kwargs.pop("columns")
    table = SimpleNamespace(**kwargs, columns=columns)

    def column(key):
        return next((c for c in columns if c.get("key") == key), None)

    table.column = column
    return table


def test_revenue_does_not_bind_to_count():
    """The production trap: a billing-count table is not quarterly revenue."""
    table = _table(
        id="ops", name="2026 H1 Operational Metrics",
        columns=(
            {"key": "c0", "name": "Metric", "type": "category",
             "samples": ("Invoices", "Credits")},
            {"key": "c1", "name": "Count", "type": "number", "unit": ""},
        ),
    )
    question = "Show quarterly revenue, operating expenses and profit for FY 2025-26"
    result = cover(question, [
        MeasureAsk("quarterly revenue", "T1", "Count"),
        MeasureAsk("operating expenses", "T1", "Count"),
        MeasureAsk("profit", "T1", "Count"),
    ], {"T1": table})
    assert result.bound == ()
    assert [m for m in result.missing] == [
        "quarterly revenue", "operating expenses", "profit",
    ]


def test_a_revenue_header_binds_and_a_missing_measure_is_named():
    table = _table(
        id="fin", name="Financials",
        columns=(
            {"key": "c0", "name": "Quarter", "type": "date"},
            {"key": "c1", "name": "Revenue (₹ lakh)", "type": "number", "unit": "₹ lakh"},
            {"key": "c2", "name": "Headcount", "type": "number", "unit": ""},
        ),
    )
    question = "Chart quarterly revenue and operating expenses"
    result = cover(question, [
        MeasureAsk("quarterly revenue", "T1", "Revenue (₹ lakh)"),
        MeasureAsk("operating expenses", "T1", "Headcount"),
    ], {"T1": table})
    assert len(result.bound) == 1
    assert result.bound[0].column_key == "c1"
    assert result.missing == ("operating expenses",)


def test_a_quote_that_is_not_in_the_question_is_refused():
    table = _table(id="t", name="T", columns=(
        {"key": "c0", "name": "Revenue", "type": "number", "unit": "₹"},
    ))
    result = cover("chart the budget", [
        MeasureAsk("secret revenue", "T1", "Revenue"),
    ], {"T1": table})
    assert result.bound == ()
    assert result.missing == ("secret revenue",)


def test_a_wide_table_binds_the_named_plan_columns():
    table = _table(id="plans", name="Plans", columns=(
        {"key": "c0", "name": "Quarter", "type": "category"},
        {"key": "c1", "name": "Starter", "type": "number", "unit": "₹"},
        {"key": "c2", "name": "Pro", "type": "number", "unit": "₹"},
        {"key": "c3", "name": "Enterprise", "type": "number", "unit": "₹"},
    ))
    question = "stacked revenue by Starter, Pro and Enterprise per quarter"
    result = cover(question, [
        MeasureAsk(question, "T1", None),
    ], {"T1": table})
    assert result.bound[0].series_keys == ("c1", "c2", "c3")


def test_pro_does_not_match_profit():
    assert not format_binds("Pro", "Profit")
    assert format_binds("quarterly revenue", "Revenue (₹ lakh)")


def test_shapes():
    assert choose_shape("combo", units=["₹", "%"], n_measures=2, has_period=True).shape == "combo"
    assert choose_shape("combo", units=["₹", "₹"], n_measures=2, has_period=False).shape == "grouped_bar"
    with pytest.raises(ValueError, match="two units"):
        choose_shape("combo", units=["₹", "%", "people"], n_measures=3, has_period=True)
    with pytest.raises(ValueError, match="stacked"):
        choose_shape("stacked_bar", units=["₹", "%"], n_measures=2, has_period=True)
    assert choose_shape("auto", units=["₹", "₹"], n_measures=2, has_period=True).shape == "line"


def test_not_available_names_what_was_checked():
    text = not_available(["quarterly revenue"], ["Q3 Board Update"])
    assert "quarterly revenue" in text
    assert "Q3 Board Update" in text
    assert "Document tables I can use" not in text
