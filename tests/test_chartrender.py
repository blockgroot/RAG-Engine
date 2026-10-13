"""The chart picture Slack gets: every shape draws, and colours match the web."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.chartrender import ChartRenderError, build_chart_renderer
from app.chartrender import matplotlib_renderer as mr
from app.config.settings import ChartRenderSettings

ROOT = Path(__file__).resolve().parents[1]
MONTHS = ["2026-03-01", "2026-01-01", "2026-02-01"]  # out of order on purpose
CATS = [f"Team {c}" for c in "ABCDEFGHIJ"]


def _p(bucket="", group=None, series=None, value=1.0):
    return {"bucket": bucket, "group": group, "series": series, "value": value}


PANELS = {
    "line": [_p(m, g, None, i + j) for i, m in enumerate(MONTHS) for j, g in enumerate(["Done", "Todo"])],
    "area": [_p(m, None, None, 10 + i) for i, m in enumerate(MONTHS)],
    "bar": [_p("", g, None, v) for g, v in [("Eng", 130), ("Sales", 80.5)]],
    "horizontal_bar": [_p("", c, None, i) for i, c in enumerate(CATS[:3])],
    "stacked_bar": [_p(m, g, None, 1 + j) for m in MONTHS for j, g in enumerate(["Done", "Todo"])],
    "grouped_bar": [_p("", c, s, 2 + j) for c in CATS[:3] for j, s in enumerate(["Q1", "Q2"])],
    "diverging_bar": [_p("", c, None, (-1) ** i * (i + 1)) for i, c in enumerate(CATS[:4])],
    "combo": [_p(m, None, s, v) for m in MONTHS for s, v in [("Revenue", 1200.0), ("Margin", 12.5)]],
    "pie": [_p("", c, None, i + 1) for i, c in enumerate(CATS)],
    "donut": [_p("", c, None, i + 1) for i, c in enumerate(CATS[:4])],
    "stacked_area": [_p(m, g, None, 1 + j) for m in MONTHS for j, g in enumerate(["ana", "raj"])],
    "heatmap": [_p(m, g, None, i + j) for i, m in enumerate(MONTHS) for j, g in enumerate(["ana", "raj"])],
}


def test_a_two_breakdown_heatmap_draws_group_by_split():
    panel = {"title": "t", "chart": "heatmap", "split_by": "c1",
             "points": [_p("", "North", "Q1", 3), _p("", "South", "Q2", 2)]}
    png = build_chart_renderer(ChartRenderSettings(width=600, height=350, dpi=72)).render_png(panel)
    assert png.startswith(b"\x89PNG")


@pytest.mark.parametrize("chart", sorted(PANELS))
def test_every_shape_draws_a_png(chart):
    panel = {"title": f"{chart} chart", "chart": chart, "unit": "₹ lakh",
             "points": PANELS[chart], "explain": "x", "caveat": "Taken from text. More.",
             "series_units": {"Revenue": "₹", "Margin": "%"}, "table": {"document": "Budget"}}
    png = build_chart_renderer(ChartRenderSettings(width=600, height=350, dpi=72)).render_png(
        panel, period="quarter")
    assert png.startswith(b"\x89PNG")


def test_many_categories_draw_as_a_ranking():
    png = build_chart_renderer(ChartRenderSettings(width=600, height=350, dpi=72)).render_png(
        {"title": "t", "chart": "bar", "points": [_p("", c, None, i) for i, c in enumerate(CATS)]})
    assert png.startswith(b"\x89PNG")


def test_combo_draws_two_value_axes(monkeypatch):
    import matplotlib.pyplot as plt

    seen = []
    real_close = plt.close
    monkeypatch.setattr(plt, "close", lambda fig: (seen.append(len(fig.axes)), real_close(fig)))
    build_chart_renderer(ChartRenderSettings(width=600, height=350, dpi=72)).render_png(
        {"title": "t", "chart": "combo", "points": PANELS["combo"],
         "series_units": {"Revenue": "₹", "Margin": "%"}})
    assert seen == [2]


def test_time_axis_is_in_time_order_and_named_by_period():
    timed, xs, _, _ = mr._pivot(PANELS["area"])
    assert timed and xs == sorted(MONTHS)
    assert mr.format_bucket("2026-04-01", "quarter") == "Q2 2026"
    assert mr.format_bucket("2026-04-01", "month") == "Apr 2026"


def test_no_points_is_a_render_error():
    with pytest.raises(ChartRenderError):
        build_chart_renderer().render_png({"title": "t", "chart": "bar", "points": []})


def test_palette_matches_the_web():
    css = (ROOT / "frontend/app/globals.css").read_text()
    series = [re.search(rf"--chart-{i}:\s*(#[0-9a-fA-F]{{6}})", css).group(1) for i in range(1, 7)]
    assert mr.SERIES_COLORS == series

    ts = (ROOT / "frontend/components/chartColors.ts").read_text()
    block = re.search(r"CATEGORY_COLORS[^{]*\{(.*?)\n\};", ts, re.S).group(1)
    pairs = dict(re.findall(r'^\s*"?([\w ]+?)"?:\s*"(#[0-9a-fA-F]{6})"', block, re.M))
    assert mr.CATEGORY_COLORS == pairs


def test_category_colours_are_semantic_and_distinct():
    colors = mr.category_colors(["Done", "Canceled", "repo-a", "repo-b", "repo-c"])
    assert colors["done"] == "#0f766e" and colors["canceled"] == "#be123c"
    assert len(set(colors.values())) == 5
