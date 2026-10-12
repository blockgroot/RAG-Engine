"""Charts drawn as pictures, for surfaces with no canvas (Slack)."""

from __future__ import annotations

from .base import ChartRenderer, ChartRenderError
from .factory import build_chart_renderer

__all__ = ["ChartRenderer", "ChartRenderError", "build_chart_renderer"]
