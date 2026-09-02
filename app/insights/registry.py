"""The metric registry -- this codebase's semantic layer.

Why a hardcoded registry rather than letting a model write SQL: pointed at raw
tables, an LLM re-derives the grain, the joins and the metric definition on
every prompt, so the same question returns different numbers. Worse, a wrong
number arrives as a bar chart, which reads as a measurement rather than an
answer -- and neither the confidence gate nor the strict prompt can check
arithmetic. So the model SELECTS from this list and never computes one.

Same discipline as ``app/llm/catalog.py``: a small, hand-kept, admitted set.
Kept honest by ``tests/test_insights_registry.py``, which forbids a format
hole, a parameter or a statement break in any fragment here.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Chart shapes the frontend can actually draw. A metric may not name one we
#: cannot render -- a registry entry that resolves to a blank panel is worse
#: than one that does not exist.
CHART_TYPES = ("line", "bar", "stacked_bar", "histogram", "stat", "table")

#: The ONLY groupings any metric may accept. A ``group_by`` becomes a SQL
#: identifier, so it can never originate in user text -- only here. Values are
#: bare column names on ``activity_facts`` and nothing else.
DIMENSIONS = {
    "actor": "actor",
    "subject": "subject",
    "state": "state",
    "provider": "provider",
    "space": "workspace_id",
}

#: ``date_trunc``'s unit. A closed set because it reaches SQL as a literal and
#: cannot be bound as a parameter.
PERIODS = ("week", "month", "quarter")


@dataclass(frozen=True)
class Metric:
    """One named, countable thing.

    ``select`` is a fixed aggregate fragment over ``activity_facts`` -- fixed,
    never formatted. The scoping (org, workspace, window) and the grouping are
    added by ``store.run_metric`` from looked-up constants and bound
    parameters, never by string building.

    ``chart`` is a DEFAULT, not a constraint: the same metric grouped by time
    is a line and grouped by ``actor`` is a leaderboard, so a dashboard panel
    pairs a metric with a grouping and picks the shape from that.
    """

    key: str
    provider: str
    label: str
    chart: str
    kind: str                       # the activity_facts.kind it counts
    select: str = "count(*)"
    dims: tuple[str, ...] = ()
    unit: str = ""
    #: Rendered under the chart. Where a metric cannot see everything (an
    #: upstream filter, an item cap), say so here -- a partial chart that looks
    #: complete is the failure that matters.
    caveat: str = ""


METRICS: dict[str, Metric] = {}


def _add(metric: Metric) -> None:
    METRICS[metric.key] = metric


# ---------------------------------------------------------------------------
# Notion & Drive -- countable from data ingest already stores.
#
# `space` and `actor` are dimensions rather than separate metrics: "pages per
# space" and "top editors" are the SAME count grouped differently, and giving
# each its own entry would mean two definitions to keep in agreement.
# ---------------------------------------------------------------------------

_add(Metric(
    key="docs_changed",
    provider="notion",
    label="Pages created or edited",
    chart="line",
    kind="doc_changed",
    dims=("space", "actor"),
    unit="pages",
))
_add(Metric(
    key="drive_docs_changed",
    provider="google",
    label="Files created or edited",
    chart="line",
    kind="doc_changed",
    dims=("space", "actor"),
    unit="files",
))

# NOT here yet, deliberately:
#
# `doc_staleness` needs the LATEST fact per document (a page edited five times
# is one page, not five), which is a DISTINCT ON rather than a GROUP BY over a
# window -- a different query shape, so it gets its own function rather than a
# second meaning for `run_metric`.
#
# `corpus_size` counts `documents`, not `activity_facts`. Same reason.


def get(key: str) -> Metric:
    """Look one up, raising rather than inventing.

    ``KeyError`` is the contract: the ask box turns it into "I cannot chart
    that, here is what I can", which is a refusal instead of a wrong chart.
    """
    return METRICS[key]


def for_provider(provider: str) -> list[Metric]:
    """Every metric a given connector can answer. Empty is a valid answer."""
    return [m for m in METRICS.values() if m.provider == provider]
