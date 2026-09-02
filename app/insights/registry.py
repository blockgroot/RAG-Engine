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
CHART_TYPES = (
    "line", "bar", "stacked_bar", "diverging_bar", "histogram", "stat", "table",
)

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
    #: Suppress any group with fewer than this many facts. Not a display
    #: nicety -- it is what makes an anonymous survey actually anonymous. On a
    #: six-person team, "3 of 4 responses in Engineering are negative"
    #: identifies people, and anyone who knows the team can work out which.
    #: Applied in SQL (a HAVING clause), never in the frontend, so no future
    #: call site can render the suppressed rows by accident.
    min_group_count: int = 0
    #: A SECOND grouping, for charts that genuinely need two dimensions: a
    #: diverging bar is topic (the row) BY sentiment label (the segment), and
    #: one grouping cannot express that. Whitelisted through ``DIMENSIONS``
    #: exactly like ``dims``, so it is still never caller text. Fixed per
    #: metric rather than requestable -- a chart that needs two dimensions
    #: needs those two.
    series_by: str = ""
    #: Restrict to org admins and space owners. Only for metrics that read
    #: individual people's expressed opinions -- a page count is not sensitive
    #: because everyone can already retrieve those pages, but a morale reading
    #: is a different kind of claim about colleagues.
    owners_only: bool = False


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

# ---------------------------------------------------------------------------
# GitHub -- read live, recorded as facts (app/insights/github_facts.py).
#
# THREE people, three metrics. Never one "activity by person" count: who raised
# a pull request, who merged it and who reviewed it are different claims, and
# summing them produces "ada did 12 things", which is not a fact anyone asked
# for. Every one of these discloses its cap, because a chart built from the
# newest N while looking complete is the failure that matters.
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

# ---------------------------------------------------------------------------
# Linear -- from the adapter's structured issue feed, recorded on the ingest
# job (app/insights/linear_facts.py).
#
# `subject` is the TEAM, so grouping by subject IS "by team" -- the slot repos
# occupy for GitHub. That is what answers the request this whole feature
# started from: task completion, aggregated by team.
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
    dims=("state", "subject", "actor"),
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
# Slack -- from the index, with the undercount said out loud.
#
# Ingest stores THREADS, not messages, and `SLACK_MIN_THREAD_CHARS` drops short
# ones -- so these counts are conversations, not message volume, and they are a
# floor rather than a total. Every Slack metric states that, because a chart
# that looks complete while undercounting is the failure that matters. Reading
# `conversations.history` live at chart time would be accurate, but it costs a
# rate-limited call per channel per page load; the honest cheap answer is the
# index plus disclosure.
#
# `subject` is the channel (see facts.py) and `actor` is whoever started the
# thread -- not who replied, which the index does not keep.
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
    dims=("subject", "actor", "space"),
    unit="conversations",
    caveat=_SLACK_CAP,
))

# ---------------------------------------------------------------------------
# Google Forms -- sentiment. The ONE metric whose numbers begin with an LLM.
#
# Every other metric counts rows a source handed us. Nothing in a form response
# says "morale", so each response is classified ONCE, in the background, and
# the LABEL is then counted like any other fact -- the chart is still a
# GROUP BY, and no model ever sees the total.
#
# Two protections, both structural rather than conventions:
#   - `min_group_count=5` suppresses small buckets IN SQL. Without it, "3 of 4
#     responses in Engineering are negative" identifies people.
#   - `owners_only` keeps it away from the whole company. A page count is not
#     sensitive; a reading of colleagues' opinions is a different claim.
#
# There is deliberately NO single company-wide sentiment score. One number
# invites a target, a target invites managing the number, and the number is an
# LLM's reading of a small sample.
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

# NOT here yet, deliberately:
#
# `doc_staleness` needs the LATEST fact per document (a page edited five times
# is one page, not five), which is a DISTINCT ON rather than a GROUP BY over a
# window -- a different query shape, so it gets its own function rather than a
# second meaning for `run_metric`.
#
# `corpus_size` counts `documents`, not `activity_facts`. Same reason.
#
# Slack's `active_hours` heatmap needs a chart shape `Chart.tsx` does not draw
# (day x hour), so it waits for that rather than shipping as a table nobody
# reads. `thread_response_time` needs per-message timestamps the index does not
# keep -- it stores a thread, not its replies.
#
# `open_pr_age` is a histogram over `now() - occurred_at` for pull requests
# still open, which needs the same DISTINCT-ON-shaped query as `doc_staleness`.
#
# The PR cycle-time BREAKDOWN (coding / waiting for review / in review /
# waiting to merge) needs review timestamps per pull request AND the first
# commit date -- two more calls each. It is the highest-value engineering chart
# there is, and it is deliberately not paid for yet.


def get(key: str) -> Metric:
    """Look one up, raising rather than inventing.

    ``KeyError`` is the contract: the ask box turns it into "I cannot chart
    that, here is what I can", which is a refusal instead of a wrong chart.
    """
    return METRICS[key]


def for_provider(provider: str) -> list[Metric]:
    """Every metric a given connector can answer. Empty is a valid answer."""
    return [m for m in METRICS.values() if m.provider == provider]
