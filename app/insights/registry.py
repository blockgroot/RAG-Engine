"""The metric registry -- this codebase's semantic layer.

A model writing SQL re-derives grain, joins and definitions every prompt, and
a wrong number drawn as a bar reads as a measurement; no gate checks
arithmetic. So the model SELECTS from this list and never computes.
``tests/test_insights_registry.py`` forbids a format hole, a parameter or a
statement break in any fragment here.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Chart shapes the frontend can actually draw. A metric may not name one we
#: cannot render -- a registry entry that resolves to a blank panel is worse
#: than one that does not exist.
CHART_TYPES = (
    "line", "area", "bar", "horizontal_bar", "pie", "donut", "stacked_bar",
    "stacked_area", "heatmap", "diverging_bar", "histogram", "stat", "table",
)


def shapes_for(*, time: bool, breakdowns: int) -> tuple[str, ...]:
    """The shapes a chart of this FORM can be drawn as, default first.

    Decided by the data (time axis? how many breakdowns?), one rule for
    activity and tables. A new shape is added here, in
    ``frontend/components/charts/toOption.ts`` and in ``app/chartrender``.
    """
    if time:
        return {
            0: ("line", "area", "bar", "pie", "donut"),
            1: ("bar", "line", "stacked_bar", "stacked_area", "heatmap",
                "pie", "donut", "horizontal_bar"),
        }.get(breakdowns, ("bar", "horizontal_bar", "heatmap"))
    return {
        0: ("bar",),
        1: ("bar", "horizontal_bar", "pie", "donut"),
    }.get(breakdowns, ("bar", "stacked_bar", "horizontal_bar", "heatmap"))

#: The ONLY groupings any metric may accept. A ``group_by`` becomes a SQL
#: identifier, so it can never originate in user text -- only here. Values are
#: bare column names on ``activity_facts`` and nothing else.
DIMENSIONS = {
    "actor": "actor",
    "subject": "subject",
    "state": "state",
    "provider": "provider",
    # NO "space" dimension: every query is scoped to ONE workspace_id, so it
    # could only ever draw one bar, and a cross-space breakdown would read rows
    # the asker's scope excludes.
}

#: ``date_trunc``'s unit. A closed set because it reaches SQL as a literal and
#: cannot be bound as a parameter.
PERIODS = ("day", "week", "month", "quarter")

#: One step finer, for when a period collapses everything into one bucket
#: (a flat line that reads as "no data").
FINER_PERIOD = {"quarter": "month", "month": "week", "week": "day"}


@dataclass(frozen=True)
class Metric:
    """One named, countable thing.

    ``select`` is a fixed aggregate fragment over ``activity_facts``, never
    formatted; scoping and grouping are added by ``store.run_metric`` from
    constants and bound parameters. ``chart`` is a default, not a constraint.
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
    #: Suppress any group with fewer facts: what makes an anonymous survey
    #: anonymous. Applied in SQL (HAVING), so no call site can show the rows.
    min_group_count: int = 0
    #: A fixed second grouping for two-dimension charts (diverging bar: topic
    #: BY sentiment). Whitelisted through ``DIMENSIONS``; never caller text.
    series_by: str = ""
    #: Org admins and space owners only: for metrics reading people's opinions,
    #: a different claim about colleagues than a page count.
    owners_only: bool = False


METRICS: dict[str, Metric] = {}


def _add(metric: Metric) -> None:
    METRICS[metric.key] = metric


#: What the `subject` column holds, per provider, so a title names the right
#: thing ("by team or repo" over Notion pages was wrong).
SUBJECT_LABELS = {
    "notion": "page",
    "google": "file",
    "slack": "channel",
    "linear": "team",
    "github": "repository",
    "forms": "topic",
}


#: Dimensions COMPUTED from a row, kept apart from bare-column ``DIMENSIONS``.
#: Fixed SQL of our own; no caller text. ``progress``: finished or not by the
#: SOURCE's state type, never state names; an untyped row is NULL, not guessed.
DERIVED_DIMENSIONS = {
    "progress": (
        "(CASE WHEN activity_facts.attrs->>'state_type' IN ('completed', 'canceled') "
        "THEN 'Closed' WHEN coalesce(activity_facts.attrs->>'state_type', '') <> '' "
        "THEN 'Open' END)"
    ),
}


def subject_label(provider: str) -> str:
    return SUBJECT_LABELS.get(provider, "subject")


#: What ``actor`` IS for each tool, where "person" would mislead: a Linear
#: issue's person is whoever it is assigned to, so an unassigned issue has
#: none -- which is a fact about the issue, not a gap in what we stored.
ACTOR_LABELS = {"linear": "assignee"}

#: What an empty ``actor`` means for each tool, shown instead of "Unknown".
BLANK_ACTOR = {"linear": "Unassigned"}


def actor_label(provider: str) -> str:
    return ACTOR_LABELS.get(provider, "person")


# ---------------------------------------------------------------------------
# Notion & Drive. `actor` is a dimension, not another metric (one definition).
# `subject` (title) is NOT offered: facts.py keeps ONE row per document, so a
# count by title is always 1. "Which pages changed?" is a list:
# `panel["details"]`.
# ---------------------------------------------------------------------------

_add(Metric(
    key="docs_changed",
    provider="notion",
    label="Pages created or edited",
    chart="line",
    kind="doc_changed",
    dims=("actor",),
    unit="pages",
))
_add(Metric(
    key="drive_docs_changed",
    provider="google",
    label="Files created or edited",
    chart="line",
    kind="doc_changed",
    dims=("actor",),
    unit="files",
))

# ---------------------------------------------------------------------------
# GitHub (app/insights/github_facts.py). Three people, three metrics: raising,
# merging and reviewing are different claims, never summed. Each discloses its
# cap: the newest N looking complete is the failure that matters.
# ---------------------------------------------------------------------------

_GITHUB_CAP = (
    "Newest 100 pull requests per repo, last 180 days; reviews read for the "
    "newest 30 of them."
)

_add(Metric(
    key="prs_opened",
    provider="github",
    label="Pull requests raised",
    chart="line",
    kind="pr_opened",
    dims=("actor", "subject", "state"),
    unit="PRs",
    caveat=_GITHUB_CAP,
))
_add(Metric(
    key="prs_merged",
    provider="github",
    label="Pull requests merged",
    chart="line",
    kind="pr_merged",
    dims=("actor", "subject"),
    unit="PRs",
    # Merged is OURS, not GitHub's: GitHub reports a merged pull request as
    # "closed", so counting off its state would count abandoned branches.
    caveat=_GITHUB_CAP,
))
_add(Metric(
    key="pr_reviewers",
    provider="github",
    label="Who reviews",
    chart="bar",
    kind="pr_reviewed",
    dims=("actor", "subject"),
    unit="PRs reviewed",
    caveat=(
        "One count per reviewer per pull request, not per comment. "
        + _GITHUB_CAP
    ),
))
_add(Metric(
    key="pr_lead_time",
    provider="github",
    label="Time from raised to merged",
    chart="line",
    kind="pr_merged",
    # Median, not mean: one pull request left open over a holiday moves a mean
    # by days and tells nobody anything about the normal case.
    select="percentile_cont(0.5) WITHIN GROUP (ORDER BY value) / 86400.0",
    dims=("subject",),
    unit="days (median)",
    caveat="Merged pull requests only - an open one has no lead time. " + _GITHUB_CAP,
))
_add(Metric(
    key="commits_by_author",
    provider="github",
    label="Commits",
    chart="bar",
    kind="commit",
    dims=("actor", "subject"),
    unit="commits",
    caveat=(
        "Newest commits per repo, a small recent slice — not the full history."
    ),
))

# ---------------------------------------------------------------------------
# Linear (app/insights/linear_facts.py). `subject` is the TEAM, so "by subject"
# is "by team".
# ---------------------------------------------------------------------------

_LINEAR_CAP = "Issues that moved in the last 180 days."

_add(Metric(
    key="issues_completed",
    provider="linear",
    label="Tasks completed",
    chart="line",
    kind="issue_completed",
    dims=("actor", "subject", "state"),
    unit="tasks",
    caveat=_LINEAR_CAP,
))
_add(Metric(
    key="issue_states",
    provider="linear",
    label="Where the work sits",
    chart="stacked_bar",
    kind="issue_state",
    dims=("state", "progress", "subject", "actor"),
    unit="issues",
    caveat=_LINEAR_CAP,
))
_add(Metric(
    key="issue_cycle_time",
    provider="linear",
    label="Time from filed to done",
    chart="line",
    kind="issue_completed",
    # Median, not mean: one issue left open over a holiday moves a mean by days
    # and says nothing about the normal case.
    select="percentile_cont(0.5) WITHIN GROUP (ORDER BY value) / 86400.0",
    dims=("subject", "actor"),
    unit="days (median)",
    caveat="Completed issues with both dates known. " + _LINEAR_CAP,
))

# ---------------------------------------------------------------------------
# Slack, from the index. Ingest stores THREADS and drops short ones, so counts
# are conversations and a floor, and every metric says so. `subject` is the
# channel; `actor` is who started the thread (replies are not kept).
# ---------------------------------------------------------------------------

_SLACK_CAP = (
    "Counts conversations, not messages. Threads shorter than the ingest "
    "minimum are not indexed, so this is a floor. Credited to whoever started "
    "each thread, not to everyone who replied."
)

_add(Metric(
    key="slack_threads",
    provider="slack",
    label="Conversations",
    chart="line",
    kind="doc_changed",
    dims=("subject", "actor"),
    unit="conversations",
    caveat=_SLACK_CAP,
))

# ---------------------------------------------------------------------------
# Google Forms sentiment: the one metric whose numbers begin with an LLM. Each
# response is classified once in the background; the label is then counted in
# SQL. `min_group_count=5` (in SQL) and `owners_only` keep people
# unidentifiable. No single company-wide score: one number invites managing it.
# ---------------------------------------------------------------------------

#: Below this, a bucket can identify individuals. Five is not tuned -- it is
#: the conventional floor for reporting survey data, and it is a floor rather
#: than a default because raising it is safe and lowering it is not.
SENTIMENT_MIN_RESPONSES = 5

_add(Metric(
    key="sentiment_by_theme",
    provider="forms",
    label="How people feel, by topic",
    # Diverging stacked bar: neutral centred, positive right, negative left,
    # so the lean is readable across many topics at once. The standard for
    # Likert data, and the only shape where every topic shares a baseline.
    chart="diverging_bar",
    kind="sentiment",
    dims=("subject",),
    series_by="state",
    unit="responses",
    min_group_count=SENTIMENT_MIN_RESPONSES,
    owners_only=True,
    caveat=(
        f"Topics with fewer than {SENTIMENT_MIN_RESPONSES} responses are "
        "hidden so nobody can be identified. Each response is classified by a "
        "model, so read the shape, not the exact counts."
    ),
))

# Not here yet: `doc_staleness` and `open_pr_age` need the latest fact per
# document (DISTINCT ON, not GROUP BY); `corpus_size` counts `documents`;
# Slack `active_hours` needs day/hour dimensions; `thread_response_time` needs
# per-message times the index does not keep; PR cycle-time breakdown needs
# review timestamps and first-commit dates per pull request.


# ---------------------------------------------------------------------------
# Attributes: every simple field a source returned, in `attrs` JSONB. Which
# fields exist is DISCOVERED (`attr_catalog.discover_scope`), not listed here.
# These are HINTS only (a name, a unit): a hint never makes a field appear,
# and the data wins over a hint's type. Keys are `fields.normalize_key` form.
# ---------------------------------------------------------------------------

ATTR_TYPES = ("category", "tags", "number")


@dataclass(frozen=True)
class Attr:
    key: str
    provider: str
    kinds: tuple[str, ...]
    type: str
    #: The noun in a title: "Tasks completed by priority".
    label: str
    #: For a number: what a sum of it is called in the chart's unit.
    unit: str = ""


_PR_KINDS = ("pr_opened", "pr_merged")
_ISSUE_KINDS = ("issue_state", "issue_completed")

ATTRS: tuple[Attr, ...] = (
    Attr("labels", "github", _PR_KINDS, "tags", "label"),
    Attr("base", "github", _PR_KINDS, "category", "target branch"),
    Attr("additions", "github", _PR_KINDS, "number", "lines added", unit="lines added"),
    Attr("deletions", "github", _PR_KINDS, "number", "lines removed", unit="lines removed"),
    Attr("changed_files", "github", _PR_KINDS, "number", "files changed", unit="files changed"),
    Attr("priority_label", "linear", _ISSUE_KINDS, "category", "priority"),
    Attr("labels", "linear", _ISSUE_KINDS, "tags", "label"),
    Attr("project", "linear", _ISSUE_KINDS, "category", "project"),
    Attr("project_milestone", "linear", _ISSUE_KINDS, "category", "milestone"),
    Attr("estimate", "linear", _ISSUE_KINDS, "number", "estimate", unit="estimate points"),
)


def attrs_for(metric: "Metric") -> tuple[Attr, ...]:
    """The HINTED attributes for this metric -- the fallback when no
    discovered set is passed (tests, and code with no database at hand)."""
    return tuple(
        a for a in ATTRS if a.provider == metric.provider and metric.kind in a.kinds
    )


def attr(metric: "Metric", key: str, attrs=None) -> Attr | None:
    pool = attrs_for(metric) if attrs is None else attrs
    return next((a for a in pool if a.key == key), None)


def get(key: str) -> Metric:
    """Look one up, raising ``KeyError`` rather than inventing: the ask box
    turns it into a refusal naming what it can chart."""
    return METRICS[key]


def for_provider(provider: str) -> list[Metric]:
    """Every metric a given connector can answer. Empty is a valid answer."""
    return [m for m in METRICS.values() if m.provider == provider]
