"""The chart-picture interface: Slack has no canvas, so a chart travels as an
image. Behind an interface so the drawing library can be swapped.
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
        ``width``/``height`` in pixels (``None``: configured size); ``period``
        formats the time axis. Raises ``ChartRenderError``."""
