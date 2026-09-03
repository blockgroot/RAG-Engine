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

from collections.abc import Iterator

from ..core.answer_sources import SOURCE_NONE
from ..core.exceptions import ProviderError
from ..core.streaming import chunk_answer
from ..insights import registry, scopes, store
from ..insights.resolve import ChartSpec, CannotChart
from .base import Agent, AgentResponse


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
        caption = panel["title"]
        if not points:
            caption = f"{caption}. Nothing recorded for that yet."
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


def _ask_title(metric, group_by: str | None) -> str:
    if not group_by:
        return metric.label
    by = {
        "actor": "person",
        "subject": "team or repo",
        "state": "state",
        "space": "space",
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
    points = store.run_metric(
        spec.metric,
        org_id=org_id,
        workspace_id=workspace_id,
        period=spec.period,
        days=days,
        group_by=spec.group_by,
    )
    begun = store.first_fact_at(
        metric.provider, org_id=org_id, workspace_id=workspace_id
    )
    panel = {
        "id": f"ask:{spec.metric}:{spec.group_by or 'time'}",
        "provider": metric.provider,
        "title": _ask_title(metric, spec.group_by),
        "chart": spec.chart,
        "group_by": spec.group_by,
        "unit": metric.unit,
        "caveat": metric.caveat,
        "points": [
            {"bucket": p.bucket, "group": p.group, "series": p.series, "value": p.value}
            for p in points
        ],
        "measured_since": begun.isoformat() if begun else None,
    }
    return panel, spec.period
