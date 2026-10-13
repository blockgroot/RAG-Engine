"""Draw a chart panel as a PNG with matplotlib.

The picture follows the web chart's rules (frontend/components/charts/toOption.ts) so a
chart reads the same in Slack as in the app: time axes stay in time order, a
long category list becomes a ranked horizontal bar, values carry their unit,
and every category keeps the colour the web gives it.

Matplotlib is imported inside ``render_png``: it is heavy, and only a Slack
chart reply ever needs it.
"""

from __future__ import annotations

import io
import logging
import re
import textwrap
from datetime import date

from ..config.settings import ChartRenderSettings
from ..insights.describe import format_value
from .base import ChartRenderer, ChartRenderError

logger = logging.getLogger(__name__)

# Mirrors `--chart-1`..`--chart-6` in frontend/app/globals.css and
# CATEGORY_COLORS in frontend/components/chartColors.ts. A test parses both
# files, so the web and the picture cannot drift apart.
SERIES_COLORS = ["#0f766e", "#2563eb", "#b45309", "#7c3aed", "#be123c", "#0e7490"]
CATEGORY_COLORS = {
    "done": "#0f766e",
    "completed": "#0f766e",
    "closed": "#0f766e",
    "in progress": "#2563eb",
    "started": "#2563eb",
    "in review": "#7c3aed",
    "todo": "#64748b",
    "backlog": "#94a3b8",
    "triage": "#b45309",
    "blocked": "#be123c",
    "canceled": "#be123c",
    "cancelled": "#be123c",
    "duplicate": "#94a3b8",
    "approved": "#0f766e",
    "changes_requested": "#be123c",
    "commented": "#64748b",
    "dismissed": "#94a3b8",
    "merged": "#7c3aed",
    "open": "#2563eb",
}

_INK = "#1f2937"
_MUTED = "#6b7280"
_GRID = "#e4e7ec"
# A category chart with more bars than this is a ranking: horizontal, sorted.
_MAX_VERTICAL_CATEGORIES = 8
_MAX_LABEL = 28
_TIME_CHARTS = {"line", "area", "stacked_area"}


def _djb2(key: str) -> int:
    """`preferredIndex` in chartColors.ts, including its 32-bit wraparound."""
    h = 5381
    data = key.encode("utf-16-le")  # charCodeAt reads UTF-16 code units
    for i in range(0, len(data), 2):
        h = (h * 33 + int.from_bytes(data[i:i + 2], "little")) & 0xFFFFFFFF
    if h >= 2**31:
        h -= 2**32
    return abs(h) % len(SERIES_COLORS)


def category_colors(names: list[str]) -> dict[str, str]:
    """`categoryColors` in chartColors.ts: semantic first, then hashed, distinct."""
    out: dict[str, str] = {}
    used: set[str] = set()
    for raw in names:
        key = (raw or "").strip().lower()
        known = CATEGORY_COLORS.get(key)
        if known and key not in out:
            out[key] = known
            used.add(known)
    for raw in names:
        key = (raw or "").strip().lower()
        if key in out:
            continue
        start = _djb2(key) if key else 0
        color = SERIES_COLORS[start]
        for step in range(len(SERIES_COLORS)):
            if color not in used:
                break
            color = SERIES_COLORS[(start + step + 1) % len(SERIES_COLORS)]
        out[key] = color
        used.add(color)
    return out


def _color(palette: dict[str, str], name: str, index: int) -> str:
    return palette.get((name or "").strip().lower(), SERIES_COLORS[index % len(SERIES_COLORS)])


def format_bucket(iso: str, period: str) -> str:
    """`formatBucket` in charts/toOption.ts, in a fixed English locale."""
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    if period == "quarter":
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    if period == "month":
        return d.strftime("%b %Y")
    if period == "year":
        return str(d.year)
    short = f"{d.strftime('%b')} {d.day}"
    return f"w/c {short}" if period == "week" else short


def _short(label: str) -> str:
    return label if len(label) <= _MAX_LABEL else label[: _MAX_LABEL - 1] + "…"


def _pivot(points: list[dict]) -> tuple[bool, list[str], list[str], dict]:
    """(timed, x keys, series names, {(x, series): value}).

    x is the time bucket when the panel has one, else the category; a series
    is the measure (``series``) or, on a time axis, the category (``group``).
    """
    timed = any(p.get("bucket") for p in points)
    xs: list[str] = []
    names: list[str] = []
    cells: dict[tuple[str, str], float] = {}
    for p in points:
        if timed:
            x, s = p.get("bucket") or "", p.get("series") or p.get("group") or ""
        else:
            x, s = p.get("group") or "", p.get("series") or ""
        if x not in xs:
            xs.append(x)
        if s not in names:
            names.append(s)
        cells[(x, s)] = cells.get((x, s), 0.0) + float(p.get("value") or 0)
    if timed:
        xs.sort()  # ISO dates: string order is time order
    return timed, xs, names, cells


def _capped(rows: list[tuple[str, float]], cap: int = len(SERIES_COLORS)) -> list[tuple[str, float]]:
    """`cappedCategories`: keep the largest, fold the rest into a named Other."""
    if len(rows) <= cap:
        return rows
    ranked = sorted(rows, key=lambda r: r[1], reverse=True)
    rest = ranked[cap - 1:]
    return ranked[: cap - 1] + [(f"Other ({len(rest)})", sum(v for _, v in rest))]


def _source_line(panel: dict) -> str:
    parts = []
    document = (panel.get("table") or {}).get("document")
    if document:
        parts.append(f"Source: {document}")
    caveat = (panel.get("caveat") or "").strip()
    if caveat:
        parts.append(re.split(r"(?<=[.!?])\s", caveat, maxsplit=1)[0])
    line = " · ".join(parts)
    return line if len(line) <= 180 else line[:179] + "…"


class MatplotlibChartRenderer(ChartRenderer):
    def __init__(self, settings: ChartRenderSettings) -> None:
        self._settings = settings

    def render_png(
        self,
        panel: dict,
        *,
        width: int | None = None,
        height: int | None = None,
        period: str = "month",
    ) -> bytes:
        points = (panel or {}).get("points") or []
        if not points:
            raise ChartRenderError("a chart with no points has nothing to draw")
        try:
            return self._draw(panel, points, width or self._settings.width,
                              height or self._settings.height, period)
        except ChartRenderError:
            raise
        except Exception as exc:  # noqa: BLE001 - any drawing failure means "use text"
            raise ChartRenderError(f"could not draw the chart: {exc}", cause=exc) from exc

    def _draw(self, panel: dict, points: list[dict], width: int, height: int, period: str) -> bytes:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter, MaxNLocator

        dpi = self._settings.dpi
        unit = (panel.get("unit") or "").strip()
        series_units = panel.get("series_units") or {}
        kind = panel.get("chart") or "bar"
        timed, xs, names, cells = _pivot(points)
        if kind == "heatmap" and panel.get("split_by"):
            # Two breakdowns: the first across, the second down, summed over
            # time -- as the web draws it.
            timed, xs, names, cells = _pivot([{**p, "bucket": ""} for p in points])
        labels = [format_bucket(x, period) if timed else _short(x or "(blank)") for x in xs]
        # Whole-number data gets whole-number ticks: "2.5 issues" is not a count.
        whole = all(float(p.get("value") or 0).is_integer() for p in points)

        fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi,
                         layout="constrained", facecolor="white")
        try:
            ax = fig.add_subplot()
            self._style(ax)
            fig.suptitle("\n".join(textwrap.wrap(panel.get("title") or "Chart", 90)),
                         x=0.01, ha="left", fontsize=13, fontweight="bold", color=_INK)
            source = _source_line(panel)
            if source:
                fig.supxlabel(source, x=0.01, ha="left", fontsize=8, color=_MUTED)

            def ticks(u: str):
                # A symbol unit (₹, %, $) is written against the number; a word
                # unit ("issues") goes on the axis label instead of every tick.
                symbol = bool(u) and not u[0].isalpha()
                # round(): a tick at 12.000000001 must not print as "12."
                return FuncFormatter(lambda v, _: format_value(round(v, 6), u if symbol else ""))

            def axis_label(u: str) -> str:
                return u if u and u[0].isalpha() else ""

            if kind in ("pie", "donut"):
                self._pie(ax, xs, names, cells, labels, unit)
            elif kind == "heatmap":
                self._heatmap(fig, ax, xs, names, cells, labels, unit)
            elif kind == "combo" and len(names) >= 2:
                self._combo(ax, xs, names, cells, labels, series_units, ticks, axis_label,
                            lambda: MaxNLocator(nbins=5, integer=whole))
            else:
                horizontal = kind == "horizontal_bar" or (
                    not timed and kind not in _TIME_CHARTS and len(xs) > _MAX_VERTICAL_CATEGORIES)
                if horizontal and not timed:
                    order = sorted(range(len(xs)), key=lambda i: sum(
                        cells.get((xs[i], s), 0.0) for s in names), reverse=True)
                    xs = [xs[i] for i in order]
                    labels = [labels[i] for i in order]
                if kind in _TIME_CHARTS and len(xs) > 1:
                    self._lines(ax, xs, names, cells, area=kind != "line",
                                stacked=kind == "stacked_area")
                else:
                    self._bars(ax, xs, names, cells, unit,
                               stacked=kind in ("stacked_bar", "diverging_bar"),
                               horizontal=horizontal)
                value_axis = ax.xaxis if horizontal else ax.yaxis
                value_axis.set_major_formatter(ticks(unit))
                value_axis.set_major_locator(MaxNLocator(nbins=5, integer=whole))
                (ax.set_xlabel if horizontal else ax.set_ylabel)(axis_label(unit), color=_MUTED)
                ax.grid(axis="x" if horizontal else "y", color=_GRID, linewidth=0.8)
                if horizontal:
                    ax.set_yticks(range(len(xs)), [labels[i] for i in range(len(xs))])
                    ax.invert_yaxis()  # the largest at the top, like a ranking
                else:
                    ax.set_xticks(range(len(xs)), labels)
                    if len(xs) > 6 or max((len(lab) for lab in labels), default=0) > 12:
                        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

            if len([n for n in names if n]) > 1 and kind not in ("pie", "donut", "heatmap"):
                handles, legend_labels = [], []
                for a in fig.axes:
                    h, lab = a.get_legend_handles_labels()
                    handles += h
                    legend_labels += lab
                # On the axes, just above the plot: constrained layout then
                # stacks title, legend and plot without overlap.
                ax.legend(handles, legend_labels, loc="lower left", bbox_to_anchor=(0, 1.0),
                          ncol=min(4, len(handles)), frameon=False, fontsize=9)

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
            return buf.getvalue()
        finally:
            plt.close(fig)

    @staticmethod
    def _style(ax) -> None:
        ax.set_facecolor("white")
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(_GRID)
        ax.tick_params(colors=_MUTED, labelsize=9, length=0)
        ax.set_axisbelow(True)

    @staticmethod
    def _bars(ax, xs, names, cells, unit, *, stacked: bool, horizontal: bool) -> None:
        n = len(names)
        single = n == 1
        palette = category_colors(xs if single else names)
        width = 0.7 if (stacked or single) else 0.8 / n
        pos = [0.0] * len(xs)
        neg = [0.0] * len(xs)
        draw = ax.barh if horizontal else ax.bar
        for i, name in enumerate(names):
            values = [cells.get((x, name), 0.0) for x in xs]
            if stacked:
                offsets = list(range(len(xs)))
                base = [neg[j] if v < 0 else pos[j] for j, v in enumerate(values)]
            else:
                shift = 0 if single else (i - (n - 1) / 2) * width
                offsets = [j + shift for j in range(len(xs))]
                base = [0.0] * len(xs)
            colors = [_color(palette, x, j) for j, x in enumerate(xs)] if single \
                else _color(palette, name, i)
            kw = {"left": base, "height": width} if horizontal else {"bottom": base, "width": width}
            bars = draw(offsets, values, color=colors, label=name or None, **kw)
            if stacked:
                for j, v in enumerate(values):
                    if v < 0:
                        neg[j] += v
                    else:
                        pos[j] += v
            if single and len(xs) <= 20:
                ax.bar_label(bars, labels=[format_value(v, unit) for v in values],
                             padding=3, fontsize=8, color=_INK)

    @staticmethod
    def _lines(ax, xs, names, cells, *, area: bool, stacked: bool = False) -> None:
        palette = category_colors(names)
        base = [0.0] * len(xs)
        for i, name in enumerate(names):
            values = [cells.get((x, name), 0.0) for x in xs]
            top = [b + v for b, v in zip(base, values)] if stacked else values
            color = _color(palette, name, i)
            ax.plot(range(len(xs)), top, color=color, linewidth=2, marker="o",
                    markersize=3.5, label=name or None)
            if area:
                ax.fill_between(range(len(xs)), base if stacked else 0, top, color=color,
                                alpha=0.35 if stacked else 0.15)
            if stacked:
                base = top

    @staticmethod
    def _heatmap(fig, ax, xs, names, cells, labels, unit) -> None:
        """Columns are the x keys, rows the series -- the web's heatmap."""
        grid = [[cells.get((x, name), 0.0) for x in xs] for name in names]
        image = ax.imshow(grid, aspect="auto", cmap="Greens", vmin=0)
        ax.set_xticks(range(len(xs)), labels)
        ax.set_yticks(range(len(names)), [_short(n or "(blank)") for n in names])
        if len(xs) * len(names) <= 60:
            peak = max((v for row in grid for v in row), default=0) or 1
            for r, row in enumerate(grid):
                for c, v in enumerate(row):
                    ax.text(c, r, format_value(v, unit), ha="center", va="center", fontsize=8,
                            color="white" if v > peak * 0.6 else _INK)
        fig.colorbar(image, ax=ax, shrink=0.8)

    @staticmethod
    def _combo(ax, xs, names, cells, labels, series_units, ticks, axis_label, locator) -> None:
        """First measure as bars on the left axis, second as a line on the right."""
        first, second = names[0], names[1]
        u1, u2 = series_units.get(first, ""), series_units.get(second, "")
        left = [cells.get((x, first), 0.0) for x in xs]
        right = [cells.get((x, second), 0.0) for x in xs]
        ax.bar(range(len(xs)), left, width=0.6, color=SERIES_COLORS[0], label=first)
        ax2 = ax.twinx()
        MatplotlibChartRenderer._style(ax2)
        ax2.spines["right"].set_visible(True)
        ax2.spines["right"].set_color(_GRID)
        ax2.plot(range(len(xs)), right, color=SERIES_COLORS[1], linewidth=2, marker="o",
                 markersize=3.5, label=second)
        for a, u, name in ((ax, u1, first), (ax2, u2, second)):
            a.yaxis.set_major_formatter(ticks(u))
            a.yaxis.set_major_locator(locator())
            a.set_ylabel(axis_label(u) or name, color=_MUTED)
        ax.grid(axis="y", color=_GRID, linewidth=0.8)
        ax.set_xticks(range(len(xs)), labels)
        if len(xs) > 6:
            for t in ax.get_xticklabels():
                t.set_rotation(30)
                t.set_ha("right")

    @staticmethod
    def _pie(ax, xs, names, cells, labels, unit) -> None:
        rows = _capped([(labels[i], sum(cells.get((x, s), 0.0) for s in names))
                        for i, x in enumerate(xs)])
        rows = [r for r in rows if r[1] > 0]
        if not rows:
            raise ChartRenderError("a pie needs at least one positive share")
        total = sum(v for _, v in rows)
        palette = category_colors([n for n, _ in rows])
        ax.pie(
            [v for _, v in rows],
            labels=[f"{n}\n{format_value(v, unit)} · {v / total:.0%}" for n, v in rows],
            colors=[_color(palette, n, i) for i, (n, _) in enumerate(rows)],
            startangle=90, counterclock=False,
            wedgeprops={"width": 0.45, "edgecolor": "white", "linewidth": 2},
            textprops={"fontsize": 9, "color": _INK},
        )
        ax.set_aspect("equal")
