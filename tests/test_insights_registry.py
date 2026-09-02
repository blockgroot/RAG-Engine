"""The registry IS the semantic layer -- these tests are what stop a chart from
containing a number nobody computed.

No DB, no network: the subject is the shape of the definitions themselves.
Pointed at raw tables, an LLM re-derives the grain, the joins and the metric
definition on every prompt, so the same question returns different numbers --
and a wrong number arrives as a bar chart, which reads as a measurement rather
than an answer. So the model selects from this list and never computes.
"""

from __future__ import annotations

import pytest

from app.insights import registry


def test_every_metric_declares_its_provider_and_chart():
    assert registry.METRICS, "an empty registry means the ask box can only refuse"
    for key, metric in registry.METRICS.items():
        assert metric.key == key, f"{key} disagrees with its own key"
        assert metric.provider, f"{key} has no provider"
        assert metric.chart in registry.CHART_TYPES, f"{key} wants an unknown chart"
        assert metric.label, f"{key} has no human label"
        assert metric.kind, f"{key} counts no kind of fact"


def test_no_metric_interpolates_anything_into_its_sql():
    """A registry entry is a FIXED fragment. The moment one accepts an f-string
    hole or a parameter, the semantic layer becomes a SQL injection surface
    with extra steps."""
    for key, metric in registry.METRICS.items():
        assert "{" not in metric.select, f"{key}.select has a format hole"
        assert "%" not in metric.select, f"{key}.select takes a parameter"
        assert ";" not in metric.select, f"{key}.select has a statement break"


def test_dimensions_are_whitelisted_column_names():
    """group_by reaches SQL as an identifier, so it can never come from user
    text -- only from this fixed set."""
    for key, metric in registry.METRICS.items():
        for dim in metric.dims:
            assert dim in registry.DIMENSIONS, f"{key} allows unknown dim {dim!r}"


def test_dimension_targets_are_bare_identifiers():
    """DIMENSIONS' values are spliced into SQL directly. Anything other than a
    plain column name here is the one place an injection could enter."""
    for name, column in registry.DIMENSIONS.items():
        assert column.isidentifier(), f"dimension {name!r} maps to {column!r}"


def test_periods_are_a_closed_set():
    """date_trunc's first argument is a literal, not a bindable parameter, so a
    caller-supplied one is an injection. Three values, forever."""
    assert set(registry.PERIODS) == {"week", "month", "quarter"}


def test_a_metric_can_be_looked_up_by_provider():
    notion = registry.for_provider("notion")
    assert notion, "Phase 0 ships Notion metrics"
    assert all(m.provider == "notion" for m in notion)


def test_drive_metrics_live_under_google_not_google_drive():
    """The provider string is `google` everywhere else in this codebase
    (SUPPORTED_PROVIDERS, oauth_connections). A second spelling here would make
    every Drive chart silently empty."""
    assert registry.for_provider("google"), "Drive metrics must be under 'google'"
    assert not registry.for_provider("google_drive")


def test_an_unknown_metric_is_not_silently_invented():
    """KeyError is the point: the ask box turns it into 'I cannot chart that,
    here is what I can', which is a refusal instead of a wrong chart."""
    with pytest.raises(KeyError):
        registry.get("definitely_not_a_metric")


def test_github_may_appear_here_but_only_as_facts():
    """GitHub metrics are allowed (it writes activity_facts rows on a
    facts-only sync path), but no metric may claim a provider we cannot
    actually count -- so every provider named here is one a connector supports."""
    known = {"notion", "google", "slack", "linear", "github", "forms"}
    for key, metric in registry.METRICS.items():
        assert metric.provider in known, f"{key} names unknown provider {metric.provider!r}"


# ---------------------------------------------------------------------------
# Panels: a metric is a definition, a panel is a view of one. The registry
# stayed small because "top editors" is `docs_changed` grouped by actor rather
# than a second definition -- which only works if every panel is checked
# against the metric it claims.
# ---------------------------------------------------------------------------


def test_every_panel_names_a_real_metric_dimension_and_chart():
    from app.insights import panels

    panels.validate()  # raises with the offending panel named


def test_a_panel_may_not_group_by_a_dimension_its_metric_forbids():
    """The check has to be real, not decorative -- so break one on purpose."""
    from dataclasses import replace

    from app.insights import panels

    original = panels.PANELS["notion"]
    bad = replace(original[0], group_by="state")  # docs_changed allows space/actor
    panels.PANELS["notion"] = (bad,)
    try:
        with pytest.raises(ValueError, match="does not allow"):
            panels.validate()
    finally:
        panels.PANELS["notion"] = original


def test_every_provider_with_panels_has_metrics_for_them():
    from app.insights import panels

    for provider in panels.PANELS:
        assert registry.for_provider(provider), f"{provider} has panels but no metrics"
