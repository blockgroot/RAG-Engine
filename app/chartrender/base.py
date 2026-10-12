"""The chart-picture interface.

Slack has no canvas, so a chart that the web draws as SVG has to travel as an
image. The renderer sits behind this interface so the drawing library can be
swapped (e.g. a server-side ECharts render) without touching the Slack flow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.exceptions import ProviderError


class ChartRenderError(ProviderError):
    """A chart panel could not be drawn; the caller falls back to text values."""


class ChartRenderer(ABC):
    @abstractmethod
    def render_png(
        self,
        panel: dict,
        *,
        width: int | None = None,
        height: int | None = None,
        period: str = "month",
    ) -> bytes:
        """Draw one chart panel (the dict `InsightsAgent` returns) as PNG bytes.

        ``width``/``height`` are pixels; ``None`` uses the configured size.
        ``period`` is the panel's time grain, which formats the time axis.
        Raises ``ChartRenderError``.
        """
