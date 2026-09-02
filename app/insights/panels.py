"""Which charts a connector actually shows, and how.

A ``Metric`` is a definition; a ``Panel`` is a *view* of one -- the same count
grouped over time is a line and grouped by ``actor`` is a leaderboard. Keeping
the two apart is what stopped the registry growing a second entry per
grouping, each with its own definition to keep in agreement.

Hardcoded and curated on purpose: nobody configures a dashboard. It exists
because a connector exists, which is why there is no "create a dashboard" step
anywhere in this feature.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import registry


@dataclass(frozen=True)
class Panel:
    """One chart on the page."""

    metric: str
    title: str
    chart: str
    group_by: str | None = None

    @property
    def id(self) -> str:
        """Stable per-panel key, so the frontend can keep React keys and the
        ask box can name a panel it reproduced."""
        return f"{self.metric}:{self.group_by or 'time'}"


#: Panels per provider, in display order. A provider with no entry simply has
#: no charts yet -- the UI says so rather than rendering an empty frame.
PANELS: dict[str, tuple[Panel, ...]] = {
    "notion": (
        Panel("docs_changed", "Pages created or edited", "line"),
        Panel("docs_changed", "Most active spaces", "bar", group_by="space"),
        Panel("docs_changed", "Top editors", "bar", group_by="actor"),
    ),
    "google": (
        Panel("drive_docs_changed", "Files created or edited", "line"),
        Panel("drive_docs_changed", "Most active spaces", "bar", group_by="space"),
        Panel("drive_docs_changed", "Top editors", "bar", group_by="actor"),
    ),
}


def for_provider(provider: str) -> tuple[Panel, ...]:
    return PANELS.get(provider, ())


def validate() -> None:
    """Every panel must name a real metric, a dimension that metric allows, and
    a chart we can draw.

    Called by the test suite rather than at import: a broken panel should fail
    a test run, not a deploy's health check.
    """
    for provider, panels in PANELS.items():
        for panel in panels:
            metric = registry.get(panel.metric)
            if metric.provider != provider:
                raise ValueError(
                    f"panel {panel.id} sits under {provider} but its metric is "
                    f"{metric.provider}"
                )
            if panel.chart not in registry.CHART_TYPES:
                raise ValueError(f"panel {panel.id} wants unknown chart {panel.chart}")
            if panel.group_by and panel.group_by not in metric.dims:
                raise ValueError(
                    f"panel {panel.id} groups by {panel.group_by!r}, which "
                    f"{metric.key} does not allow"
                )
