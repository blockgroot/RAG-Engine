"""``/insights``: the charts behind the Visualizations section.

Member-level (a chart aggregates rows the asker can already read), but
never wider: ``org_id``/``user_id`` come from the session and a space goes
through ``assert_member``. Every number is SQL over ``activity_facts``;
the model only picks registry keys.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..core.exceptions import AuthError, ProviderError
from ..insights import panels as panel_defs
from ..insights import pins, registry, resolve, scopes
from ..insights import store as insight_store
from ..security.rate_limit import limit_question
from ..workspaces.store import assert_member
from .deps import SessionClaims, get_session, viewer_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/insights", tags=["insights"])

#: How far back a dashboard looks, per period. Not the same as the bucket size:
#: a weekly view of one week is a single bar, which tells nobody anything.
_WINDOW_DAYS = {"day": 45, "week": 84, "month": 365, "quarter": 730}

#: A question, not an essay. Long enough for a real multi-clause request, short
#: enough that it cannot bloat the resolution prompt.
MAX_QUESTION_CHARS = 400


def _scope(session: SessionClaims, scope: str | None) -> str | None:
    """Resolve ``scope`` to a workspace id, or None for the company.

    A space the caller is not in is 403, never an empty chart.
    """
    if scope in (None, "", "org"):
        return None
    try:
        assert_member(scope, session.org_id, session.user_id)
    except AuthError:
        raise HTTPException(status_code=403, detail="Not a member of that space.")
    return scope


def _may_see(metric, session: SessionClaims, workspace_id: str | None) -> bool:
    """Whether this caller may see an ``owners_only`` metric (sentiment).

    Colleagues' opinions stay with org admins (everywhere) and a space's owner
    (in that space).
    """
    return scopes.may_see_metric(
        metric,
        role=session.role,
        workspace_id=workspace_id,
        org_id=session.org_id,
        user_id=session.user_id,
    )


def _period(period: str) -> str:
    if period not in registry.PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown period. Expected one of: {', '.join(registry.PERIODS)}.",
        )
    return period


@router.get("/scopes")
def list_scopes(session: SessionClaims = Depends(get_session)):
    """The company plus every space this member is in, each with its providers.

    A space with no providers is still returned, so it stays in the picker.
    """
    found = scopes.member_scopes(session.org_id, session.user_id)
    return {
        "scopes": [
            {
                "id": s.id,
                "name": s.name,
                "providers": s.providers,
                # Which providers can chart yet, so "connected but no charts" is visible.
                "chartable": [p for p in s.providers if panel_defs.for_provider(p)],
            }
            for s in found
        ]
    }


@router.get("/dashboard")
def dashboard(
    scope: str | None = Query(default=None),
    period: str = Query(default="month"),
    session: SessionClaims = Depends(get_session),
):
    """Every chart for one scope in one round trip (no dashboard arriving in pieces)."""
    workspace_id = _scope(session, scope)
    period = _period(period)
    days = _WINDOW_DAYS[period]

    found = next(
        (s for s in scopes.member_scopes(session.org_id, session.user_id)
         if s.id == workspace_id),
        None,
    )
    if found is None:
        raise HTTPException(status_code=404, detail="No such scope.")

    # Resolved once for every panel: each count is narrowed to documents this
    # person may open, the same rule retrieval applies.
    viewer = viewer_for(session)
    out = []
    for provider in found.providers:
        for panel in panel_defs.for_provider(provider):
            # Omitted, not empty: an empty sentiment chart still says sentiment is measured.
            if not _may_see(registry.get(panel.metric), session, workspace_id):
                continue
            try:
                points = insight_store.run_metric(
                    panel.metric,
                    org_id=session.org_id,
                    workspace_id=workspace_id,
                    period=period,
                    days=days,
                    group_by=panel.group_by,
                    viewer=viewer,
                )
            except ProviderError:
                # One broken panel reports itself unavailable; it must not blank the page.
                logger.warning("insights: panel %s failed", panel.id, exc_info=True)
                out.append(_panel_payload(provider, panel, None, None))
                continue

            begun = insight_store.first_fact_at(
                provider, org_id=session.org_id, workspace_id=workspace_id,
                viewer=viewer,
            )
            out.append(_panel_payload(provider, panel, points, begun))

    return {
        "scope": workspace_id,
        "scope_name": found.name,
        "period": period,
        "window_days": days,
        "panels": out,
    }


def _panel_payload(provider, panel, points, begun):
    metric = registry.get(panel.metric)
    return {
        "id": panel.id,
        "provider": provider,
        "title": panel.title,
        "chart": panel.chart,
        "group_by": panel.group_by,
        "unit": metric.unit,
        "caveat": metric.caveat,
        # None means the panel failed; [] means it ran and there is nothing to
        # show. The frontend must say different things for those.
        "points": None if points is None
        else [
            {"bucket": p.bucket, "group": p.group, "series": p.series,
             "value": p.value}
            for p in points
        ],
        # Facts start at the first sync; without this the axis looks like nobody
        # worked before it.
        "measured_since": begun.isoformat() if begun else None,
    }


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    scope: str | None = None


def answer_chart_question(
    question: str,
    *,
    session: SessionClaims,
    workspace_id: str | None,
) -> dict:
    """Turn a question into ONE chart, or a refusal naming what is available.

    Shared by ``POST /insights/ask`` and chat; the model picks a registry key,
    never SQL or a number.
    """
    found = next(
        (s for s in scopes.member_scopes(session.org_id, session.user_id)
         if s.id == workspace_id),
        None,
    )
    if found is None:
        raise HTTPException(status_code=404, detail="No such scope.")

    try:
        spec = resolve.resolve_question(
            question, providers=list(found.providers)
        )
    except resolve.CannotChart as exc:
        # "I can't chart that, here is what I can" is an ANSWER, not an error.
        return {"charted": False, "message": str(exc)}

    if not _may_see(registry.get(spec.metric), session, workspace_id):
        # "Unavailable", not "forbidden": forbidden confirms sentiment is collected.
        return {
            "charted": False,
            "message": "I can't chart that here.",
        }

    # The same runner chat uses; calling `run_metric` here dropped `focus`.
    from ..agent.insights_agent import _run_spec

    try:
        panel, period = _run_spec(
            spec, org_id=session.org_id, workspace_id=workspace_id,
            user_id=session.user_id, role=session.role,
            viewer=viewer_for(session),
        )
    except resolve.CannotChart as exc:
        return {"charted": False, "message": str(exc)}
    except ProviderError:
        logger.warning("insights: ask ran %s and failed", spec.metric, exc_info=True)
        raise HTTPException(status_code=502, detail="Could not run that chart.")

    return {
        "charted": True,
        "spec": {**resolve.spec_to_dict(spec), "period": period},
        "panel": panel,
    }


@router.post("/ask")
def ask(
    body: AskRequest,
    request: Request,
    session: SessionClaims = Depends(get_session),
):
    """Turn a question into ONE chart, or refuse and say what is available.

    Rate-limited per org: this route spends a model request.
    """
    limit_question(session)
    workspace_id = _scope(session, body.scope)
    return answer_chart_question(
        body.question, session=session, workspace_id=workspace_id
    )


def _ask_title(metric, group_by: str | None) -> str:
    """A title built from the registry, never the question (untrusted text in a heading)."""
    if not group_by:
        return metric.label
    by = {"actor": "person", "subject": registry.subject_label(metric.provider),
          "state": "state", "provider": "app"}.get(group_by, group_by)
    return f"{metric.label} by {by}"


# --------------------------------------------------------------------------
# Pins. Personal, like a scheduler.
# --------------------------------------------------------------------------


class PinRequest(BaseModel):
    metric: str = Field(min_length=1, max_length=64)
    group_by: str | None = None
    period: str = "month"
    scope: str | None = None


@router.get("/pins")
def list_pins(session: SessionClaims = Depends(get_session)):
    return {"pins": pins.list_pins(session.org_id, session.user_id)}


@router.post("/pins", status_code=201)
def create_pin(body: PinRequest, session: SessionClaims = Depends(get_session)):
    """Keep a chart, validated against the registry, not trusted from the body.

    A pin re-runs on every page load, so a bad value would persist.
    """
    workspace_id = _scope(session, body.scope)
    try:
        metric = registry.get(body.metric)
    except KeyError:
        raise HTTPException(status_code=400, detail="No such chart.")
    if body.period not in registry.PERIODS:
        raise HTTPException(status_code=400, detail="Unknown period.")
    if body.group_by is not None and body.group_by not in metric.dims:
        raise HTTPException(status_code=400, detail="That chart cannot be grouped that way.")
    if not _may_see(metric, session, workspace_id):
        # Checked here and on every read: a demoted owner must not keep a sentiment pin.
        raise HTTPException(status_code=403, detail="Not available in this scope.")

    pin_id = pins.create_pin(
        session.org_id, session.user_id,
        workspace_id=workspace_id,
        metric=body.metric,
        group_by=body.group_by,
        period=body.period,
        title=_ask_title(metric, body.group_by),
    )
    return {"id": pin_id}


@router.delete("/pins/{pin_id}", status_code=204)
def delete_pin(pin_id: str, session: SessionClaims = Depends(get_session)):
    if not pins.delete_pin(session.org_id, session.user_id, pin_id):
        raise HTTPException(status_code=404, detail="No such pin.")


@router.get("/freshness")
def freshness(
    scope: str | None = Query(default=None),
    session: SessionClaims = Depends(get_session),
):
    """Last sync per connector, so a number can be judged current.

    ``needs_reauth`` is separate: auto-sync skips a dead token, so waiting never fixes it.
    """
    workspace_id = _scope(session, scope)
    rows = scopes.freshness(
        session.org_id, user_id=session.user_id, workspace_id=workspace_id
    )
    return {
        "connectors": [
            {
                "provider": r.provider,
                "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
                "needs_reauth": r.needs_reauth,
                "chartable": bool(panel_defs.for_provider(r.provider)),
            }
            for r in rows
        ]
    }
