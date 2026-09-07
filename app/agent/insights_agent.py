"""The Insights agent: counted charts, never RAG.

Same ``Agent`` contract as GitHub: question in, structured response out, no
``RagPipeline``. Grounding is structural — numbers come from SQL over
``activity_facts``. The model only selected the registry key (and optionally a
shape we can draw); it never produced a total.

A chart-shaped question that cannot be counted returns the refusal as the
answer and ``grounded=False``. Falling through to retrieval would invent a
number from chunk text.
"""

from __future__ import annotations

import logging
from datetime import datetime
from collections.abc import Iterator

from ..core.answer_sources import SOURCE_NONE
from ..core.exceptions import ProviderError
from ..core.streaming import chunk_answer
from ..insights import registry, scopes, store
from ..insights.facts import DOCUMENT_PROVIDERS, record_document_facts
from ..insights.resolve import ChartSpec, CannotChart
from .base import Agent, AgentResponse

logger = logging.getLogger(__name__)


class InsightsAgent(Agent):
    """Answers visual Ask turns by running a validated ``ChartSpec``."""

    def answer(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        spec: ChartSpec | dict | None = None,
        refusal: str | None = None,
        user_id: str | None = None,
        role: str | None = None,
    ) -> AgentResponse:
        del conversation_id, question  # Spec is already resolved; question is untrusted.
        if refusal:
            return AgentResponse(
                answer=refusal, grounded=False, source=SOURCE_NONE, chart=None
            )
        parsed = _as_spec(spec)
        if parsed is None:
            return AgentResponse(
                answer=(
                    "I can't chart that. Charts count activity from your "
                    "connected apps, not topics inside a document. Ask as a "
                    "normal question if you want the file's contents."
                ),
                grounded=False,
                source=SOURCE_NONE,
                chart=None,
            )
        try:
            panel, period = _run_spec(
                parsed,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id or "",
                role=role or "member",
            )
        except CannotChart as exc:
            return AgentResponse(
                answer=str(exc), grounded=False, source=SOURCE_NONE, chart=None
            )
        except ProviderError:
            return AgentResponse(
                answer="Could not run that chart.",
                grounded=False,
                source=SOURCE_NONE,
                chart=None,
            )

        points = panel.get("points") or []
        if not points:
            panel, period = _backfill_and_retry(
                parsed, panel, period,
                org_id=org_id, workspace_id=workspace_id,
                user_id=user_id or "", role=role or "member",
            )
            points = panel.get("points") or []

        caption = _caption(
            parsed, panel, points,
            org_id=org_id, workspace_id=workspace_id,
            days=scopes.WINDOW_DAYS.get(period, 0),
            metric=registry.METRICS.get(parsed.metric),
        )
        return AgentResponse(
            answer=caption,
            grounded=True,
            source=panel["provider"],
            chart=panel,
            chart_period=period,
        )

    def answer_stream(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        spec: ChartSpec | dict | None = None,
        refusal: str | None = None,
        user_id: str | None = None,
        role: str | None = None,
    ) -> tuple[Iterator[str], AgentResponse]:
        response = self.answer(
            question,
            org_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            spec=spec,
            refusal=refusal,
            user_id=user_id,
            role=role,
        )
        return chunk_answer(response.answer), response


def _as_spec(spec: ChartSpec | dict | None) -> ChartSpec | None:
    if spec is None:
        return None
    if isinstance(spec, ChartSpec):
        return spec
    try:
        return ChartSpec(
            metric=spec["metric"],
            group_by=spec.get("group_by"),
            period=spec["period"],
            chart=spec["chart"],
        )
    except (KeyError, TypeError):
        return None


def _backfill_and_retry(
    spec: ChartSpec,
    panel: dict,
    period: str,
    *,
    org_id: str,
    workspace_id: str | None,
    user_id: str,
    role: str,
) -> tuple[dict, str]:
    """Fill facts from the index for this scope, then re-run the metric.

    Ask must not wait for the next ingest (or a healthy tick) to notice
    that ``documents`` already has rows. GitHub has no documents, so this
    is a no-op for those metrics.
    """
    try:
        metric = registry.get(spec.metric)
    except KeyError:
        return panel, period
    if metric.provider not in DOCUMENT_PROVIDERS:
        return panel, period
    try:
        record_document_facts(
            org_id, provider=metric.provider, workspace_id=workspace_id,
        )
    except Exception:  # noqa: BLE001 - empty chart beats a failed answer
        logger.warning(
            "insights: lazy fact backfill failed for %s org %s",
            metric.provider, org_id, exc_info=True,
        )
        return panel, period
    try:
        return _run_spec(
            spec, org_id=org_id, workspace_id=workspace_id,
            user_id=user_id, role=role,
        )
    except (CannotChart, ProviderError):
        return panel, period


def _details(spec, *, org_id, workspace_id, days, focus) -> list[dict]:
    """Never fatal: a chart without its rows is still a chart, and losing the
    answer to keep the annotation would be the wrong trade."""
    try:
        facts = store.list_facts(
            spec.metric, org_id=org_id, workspace_id=workspace_id,
            days=days, focus=focus,
        )
    except (ProviderError, KeyError):
        logger.warning("insights: could not read details of %s", spec.metric)
        return []
    return [
        {
            "subject": f.subject,
            "actor": f.actor,
            "state": f.state,
            "at": f.occurred_at,
            "url": f.url,
        }
        for f in facts
    ]


def _buckets(points) -> list[str]:
    seen = []
    for point in points:
        if point.bucket not in seen:
            seen.append(point.bucket)
    return seen


def _span_days(points) -> int:
    """How far apart the rows we already found are.

    A finer period comes with a shorter default window, and narrowing the
    window while narrowing the bucket can drop the very rows we are trying to
    show -- July's commits are outside a 45-day daily window in September.
    """
    buckets = _buckets(points)
    if not buckets:
        return 0
    try:
        stamps = [datetime.fromisoformat(b) for b in buckets]
    except ValueError:
        return 0
    oldest = min(stamps)
    now = datetime.now(oldest.tzinfo) if oldest.tzinfo else datetime.now()
    return max(0, (now - oldest).days + 2)


def _resolve_focus(spec, metric, *, org_id, workspace_id, days) -> str | None:
    """Match what the member named against subjects that actually have rows.

    Refuses BY NAME when nothing matches, and lists what does exist. That is
    the difference between "I don't have a DAO repository with commits — I
    have 18-sana/Chain-Guard" and an empty chart, which reads as "nobody
    worked" rather than "you asked for something that isn't there".
    """
    wanted = spec.focus.strip().lower()
    try:
        subjects = store.list_subjects(
            spec.metric, org_id=org_id, workspace_id=workspace_id, days=days
        )
    except ProviderError:
        # Cannot verify, so do not filter. A whole chart beats a wrong one.
        logger.warning("insights: could not list subjects of %s", spec.metric)
        return None

    if not subjects:
        return None

    exact = [s for s in subjects if s.lower() == wanted]
    if exact:
        return exact[0]

    # Compared with punctuation and spacing removed: a member types "chain
    # guard" or "chain-guard" for a repo stored as "18-sana/Chain-Guard", and
    # a match that fails on a hyphen is indistinguishable from no such repo.
    key = _squash(wanted)
    if not key:
        return None
    # Both directions: "DAO" is inside "18-sana/DAO", and someone may paste
    # the full "18-sana/Chain-Guard" for a subject stored bare.
    partial = [
        s for s in subjects
        if key in _squash(s) or (_squash(s) and _squash(s) in key)
    ]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise CannotChart(
            f"\"{spec.focus}\" matches more than one: "
            f"{_listed(partial)}. Which one?"
        )
    raise CannotChart(
        f"I have no {metric.label.lower()} for \"{spec.focus}\" in the last "
        f"{days} days. What I do have: {_listed(subjects)}."
    )


def _squash(value: str) -> str:
    """Letters and digits only, lowercased. `#rag-updates` -> `ragupdates`."""
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _listed(values: list[str], limit: int = 6) -> str:
    shown = values[:limit]
    rest = len(values) - len(shown)
    text = ", ".join(shown)
    return f"{text} and {rest} more" if rest > 0 else text


def _empty_caption(spec, title, *, org_id, workspace_id, days, metric) -> str:
    """Say WHICH kind of empty this is. There are three, and they need
    different actions from the member.

    "Nothing recorded yet" for all three sent people to wait for a sync that
    would never change anything -- the commonest real case is activity that
    exists just outside the window, where the fix is one word in the question.
    """
    if metric is None:
        return f"{title}. Nothing recorded for that yet."

    began = None
    if org_id:
        try:
            began = store.first_fact_at(
                metric.provider, org_id=org_id, workspace_id=workspace_id
            )
        except ProviderError:
            began = None

    if began is None:
        # Nothing from this connector has EVER been counted in this scope.
        return (
            f"{title}. Nothing from {metric.provider.title()} has been counted "
            "in this space yet. If it was connected before charts existed, the "
            "next sync starts counting — there is nothing to fix."
        )

    # There ARE facts from this provider, just not of this kind in this window.
    wider = _has_older_rows(metric, org_id=org_id, workspace_id=workspace_id)
    if wider:
        return (
            f"{title}. Nothing in the last {days} days, but there IS older "
            f"activity — the earliest is {began.date().isoformat()}. Ask for "
            "it quarterly to widen the window."
        )
    return (
        f"{title}. No {metric.unit or 'activity'} recorded in the last "
        f"{days} days. Other {metric.provider.title()} activity has been "
        f"counted since {began.date().isoformat()}, so the connection is "
        "working — this particular thing simply has not happened."
    )


def _has_older_rows(metric, *, org_id: str, workspace_id: str | None) -> bool:
    """Whether this exact metric has rows beyond the widest chart window.

    Answers the question the member actually has -- "is it missing, or am I
    looking at the wrong period?" -- and it is the case that bit a real
    tenant: 23 pull requests existed, every one of them older than the
    recording window, and the chart said "nothing recorded yet".
    """
    try:
        points = store.run_metric(
            metric.key, org_id=org_id, workspace_id=workspace_id,
            period="quarter", days=max(scopes.WINDOW_DAYS.values()),
        )
    except (ProviderError, ValueError, KeyError):
        return False
    return bool(points)


def _caption(
    spec: ChartSpec, panel: dict, points: list, *,
    org_id: str = "", workspace_id: str | None = None, days: int = 0,
    metric=None,
) -> str:
    title = panel["title"]
    if not points:
        return _empty_caption(
            spec, title, org_id=org_id, workspace_id=workspace_id,
            days=days, metric=metric,
        )
    if spec.group_by == "actor" and all(not p.get("group") for p in points):
        return (
            f"{title}. Editor names were not stored when these were first "
            "indexed, so this is a total rather than a breakdown by person. "
            "The next sync will start capturing who edited."
        )
    return title


def _ask_title(metric, group_by: str | None) -> str:
    if not group_by:
        return metric.label
    by = {
        "actor": "person",
        "subject": registry.subject_label(metric.provider),
        "state": "state",
        "provider": "app",
    }.get(group_by, group_by)
    return f"{metric.label} by {by}"


def _run_spec(
    spec: ChartSpec,
    *,
    org_id: str,
    workspace_id: str | None,
    user_id: str,
    role: str,
) -> tuple[dict, str]:
    try:
        metric = registry.get(spec.metric)
    except KeyError as exc:
        raise CannotChart("No such chart.") from exc

    if not scopes.may_see_metric(
        metric, role=role, workspace_id=workspace_id, org_id=org_id, user_id=user_id
    ):
        raise CannotChart("I can't chart that here.")

    days = scopes.WINDOW_DAYS.get(spec.period, scopes.WINDOW_DAYS["month"])
    group_by = spec.group_by
    chart = spec.chart

    # "commits in the DAO repo" narrows; it does not group. Resolved against
    # the subjects that ACTUALLY have rows, so an unmatched name is refused BY
    # NAME instead of quietly filtering to nothing -- an empty chart reads as
    # "no activity" when the truth is "no such repository".
    focus = None
    if spec.focus:
        focus = _resolve_focus(spec, metric, org_id=org_id,
                               workspace_id=workspace_id, days=days)
    if chart == "pie" and group_by is None:
        # A pie needs groups to be shares OF something. Without one it is a
        # single full circle, which states nothing.
        chart = "line"
    period = spec.period
    points = store.run_metric(
        spec.metric,
        org_id=org_id,
        workspace_id=workspace_id,
        period=period,
        days=days,
        group_by=group_by,
        focus=focus,
    )

    # A period that puts EVERYTHING in one bucket draws as a single point --
    # a flat line, or a pie with one slice -- and reads as "no data" when the
    # data is fine and the bucket was too wide. Four commits on two days are
    # one bar at week AND at month. Step finer until the shape shows what
    # happened, at most twice: this is the same rows re-bucketed, never a
    # different question, and the caption says which period was used.
    refined = period
    while (
        len(_buckets(points)) <= 1
        and registry.FINER_PERIOD.get(refined)
        and refined != registry.FINER_PERIOD.get(refined)
    ):
        finer = registry.FINER_PERIOD[refined]
        finer_window = max(scopes.WINDOW_DAYS.get(finer, days), _span_days(points))
        try:
            candidate = store.run_metric(
                spec.metric, org_id=org_id, workspace_id=workspace_id,
                period=finer, days=finer_window,
                group_by=group_by, focus=focus,
            )
        except (ProviderError, ValueError):
            break
        refined = finer
        if len(_buckets(candidate)) > len(_buckets(points)):
            points, period, days = candidate, finer, finer_window
            if len(_buckets(points)) > 1:
                break

    # `days` stays the window that ACTUALLY produced these points, never the
    # refined period's default. Resetting it to the default silently emptied
    # the detail rows: July's commits are outside a 45-day daily window in
    # September, so the chart had four bars and nothing behind them.
    begun = store.first_fact_at(
        metric.provider, org_id=org_id, workspace_id=workspace_id
    )
    title = _ask_title(metric, group_by)
    if focus:
        # In the title, because a filtered chart that looks unfiltered is the
        # same failure as charting the wrong thing.
        title = f"{title} — {focus}"

    panel = {
        "id": f"ask:{spec.metric}:{group_by or 'time'}:{focus or 'all'}",
        "provider": metric.provider,
        "title": title,
        "focus": focus,
        "chart": chart,
        "group_by": group_by,
        "unit": metric.unit,
        "caveat": metric.caveat,
        "points": [
            {"bucket": p.bucket, "group": p.group, "series": p.series, "value": p.value}
            for p in points
        ],
        # The rows the bars are made of. "4 commits" answers how many and
        # nothing else; which ones, by whom and where to read them are the
        # questions that follow immediately, and every column is already on
        # the counted row.
        "details": _details(
            spec, org_id=org_id, workspace_id=workspace_id, days=days, focus=focus
        ),
        "measured_since": begun.isoformat() if begun else None,
    }
    return panel, period
