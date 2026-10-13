"""Single construction point for the chart renderer."""

from __future__ import annotations

from ..config.settings import ChartRenderSettings
from .base import ChartRenderer
from .matplotlib_renderer import MatplotlibChartRenderer


def build_chart_renderer(settings: ChartRenderSettings | None = None) -> ChartRenderer:
    """The configured renderer. Matplotlib is imported only when a chart is drawn."""
    return MatplotlibChartRenderer(settings or ChartRenderSettings.from_env())
