"""``/insights`` — the charts behind the Visualizations section.

Member-level, like ``/schedulers``: a chart aggregates rows the asker can
already retrieve in prose, so gating it behind ``require_admin`` would be
theatre. What it is NOT allowed to do is widen what they can see, which is why
``org_id`` and ``user_id`` come only from the signed session and a space scope
goes through ``assert_member`` before any query runs.

Every number here is computed by SQL over ``activity_facts``. No route on this
router calls an LLM, and none ever should without the registry in between — a
prose answer that is wrong hedges and cites, a bar chart that is wrong reads as
a measurement.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..core.exceptions import AuthError, ProviderError
from ..insights import panels as panel_defs
from ..insights import pins, registry, resolve, scopes
from ..insights import store as insight_store
from ..security.rate_limit import check_rate_limit
from ..workspaces.store import assert_member
from .deps import SessionClaims, get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/insights", tags=["insights"])

#: How far back a dashboard looks, per period. Not the same as the bucket size:
#: a weekly view of one week is a single bar, which tells nobody anything.
_WINDOW_DAYS = {"week": 84, "month": 365, "quarter": 730}

#: A question, not an essay. Long enough for a real multi-clause request, short
#: enough that it cannot bloat the resolution prompt.
MAX_QUESTION_CHARS = 400


def _scope(session: SessionClaims, scope: str | None) -> str | None:
    """Resolve the ``scope`` query parameter to a workspace id, or None.

    ``None``/``"org"`` mean the company. Anything else is treated as a
    workspace id and must survive ``assert_member`` — the one place membership
    is checked. A caller passing a space they are not in gets 403, never an
    empty chart, because empty reads as "nothing happened there".
    """
    if scope in (None, "", "org"):
        return None
    try:
        assert_member(scope, session.org_id, session.user_id)
    except AuthError:
        raise HTTPException(status_code=403, detail="Not a member of that space.")
    return scope


def _may_see(metric, session: SessionClaims, workspace_id: str | None) -> bool:
    """Whether this caller may see an ``owners_only`` metric.

    Only sentiment is gated, and not because counting is privileged: everything
    else aggregates rows the asker can already retrieve in prose. A reading of
    colleagues' expressed opinions is a different kind of claim, so it stays
    with the people already accountable for the space or the org.

    An org admin qualifies everywhere; a space owner qualifies in their own
    space. Membership itself was already checked by ``_scope``.
    """
    if not metric.owners_only:
        return True
    if session.role == "admin":
        return True
    if workspace_id is None:
        return False
    try:
        return assert_member(workspace_id, session.org_id, session.user_id) == "owner"
    except AuthError:
        return False


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

    A space with an empty ``providers`` list is still returned: "this space has
    nothing connected" is an answer, and dropping it makes the space silently
    vanish from the picker.
    """
    found = scopes.member_scopes(session.org_id, session.user_id)
    return {
        "scopes": [
            {
                "id": s.id,
                "name": s.name,
                "providers": s.providers,
                # Which of this scope's providers we can actually chart yet.
                # Stated separately so "Drive is connected but has no charts"
                # is visible rather than looking like an empty dashboard.
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
    """Every chart for one scope, already computed.

    One round trip on purpose. Six panels firing six requests means six cold
    starts on a free instance, and a dashboard that arrives in pieces reads as
    broken rather than slow.
    """
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

    out = []
    for provider in found.providers:
        for panel in panel_defs.for_provider(provider):
            # Omitted entirely rather than returned empty: an empty sentiment
            # chart would still announce that sentiment is being measured, to
            # exactly the people it is being measured on.
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
                )
            except ProviderError:
                # One broken panel must not blank the page. It reports itself
                # as unavailable, which is honest; omitting it would read as
                # "no activity".
                logger.warning("insights: panel %s failed", panel.id, exc_info=True)
                out.append(_panel_payload(provider, panel, None, None))
                continue

            begun = insight_store.first_fact_at(
                provider, org_id=session.org_id, workspace_id=workspace_id
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
        # Facts only exist from the first sync after this shipped, and author
        # names cannot be backfilled at all. Without this the axis silently
        # starts on deploy day and reads as if nobody worked before it.
        "measured_since": begun.isoformat() if begun else None,
    }


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    scope: str | None = None


@router.post("/ask")
def ask(
    body: AskRequest,
    request: Request,
    session: SessionClaims = Depends(get_session),
):
    """Turn a question into ONE chart, or refuse and say what is available.

    Not a chat endpoint: one turn, no history, no conversation id. The model
    only selects a registry key -- it never writes SQL and never emits a
    number, so the worst case is a refusal rather than a wrong chart.

    Rate-limited per org like every other LLM-backed route: this is the only
    place in the section that spends a model request.
    """
    check_rate_limit(f"insights-ask:{session.org_id}")

    workspace_id = _scope(session, body.scope)
    found = next(
        (s for s in scopes.member_scopes(session.org_id, session.user_id)
         if s.id == workspace_id),
        None,
    )
    if found is None:
        raise HTTPException(status_code=404, detail="No such scope.")

    try:
        spec = resolve.resolve_question(
            body.question, providers=list(found.providers)
        )
    except resolve.CannotChart as exc:
        # 200, not 4xx: "I can't chart that, here is what I can" is an ANSWER,
        # and the frontend renders it as guidance rather than an error banner.
        return {"charted": False, "message": str(exc)}

    if not _may_see(registry.get(spec.metric), session, workspace_id):
        # Phrased as unavailable rather than forbidden. "You are not allowed to
        # see the sentiment chart" confirms it exists and is being collected,
        # which is the thing the gate is protecting.
        return {
            "charted": False,
            "message": "I can't chart that here.",
        }

    days = _WINDOW_DAYS[spec.period]
    try:
        points = insight_store.run_metric(
            spec.metric,
            org_id=session.org_id,
            workspace_id=workspace_id,
            period=spec.period,
            days=days,
            group_by=spec.group_by,
        )
    except ProviderError:
        logger.warning("insights: ask ran %s and failed", spec.metric, exc_info=True)
        raise HTTPException(status_code=502, detail="Could not run that chart.")

    metric = registry.get(spec.metric)
    begun = insight_store.first_fact_at(
        metric.provider, org_id=session.org_id, workspace_id=workspace_id
    )
    return {
        "charted": True,
        "spec": {
            "metric": spec.metric,
            "group_by": spec.group_by,
            "period": spec.period,
        },
        "panel": {
            "id": f"ask:{spec.metric}:{spec.group_by or 'time'}",
            "provider": metric.provider,
            "title": _ask_title(metric, spec.group_by),
            "chart": spec.chart,
            "group_by": spec.group_by,
            "unit": metric.unit,
            "caveat": metric.caveat,
            "points": [
                {"bucket": p.bucket, "group": p.group, "series": p.series,
                 "value": p.value}
                for p in points
            ],
            "measured_since": begun.isoformat() if begun else None,
        },
    }


def _ask_title(metric, group_by: str | None) -> str:
    """A title the member can recognise as their own question's answer.

    Built from the registry rather than echoing the question back: a question
    is untrusted text, and putting it in a heading is how a heading becomes an
    injection surface for whoever reads the page next.
    """
    if not group_by:
        return metric.label
    by = {"actor": "person", "subject": "team or repo", "state": "state",
          "space": "space", "provider": "app"}.get(group_by, group_by)
    return f"{metric.label} by {by}"


# --------------------------------------------------------------------------
# Pins. Personal, like a scheduler -- never published to anyone.
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
    """Keep a chart. Validated against the registry, not trusted from the body.

    A pin is re-run on every page load, so an unvalidated one would be a
    stored request to `run_metric` with caller-controlled identifiers -- the
    one place in this feature where a bad value would persist rather than fail
    once.
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
        # A pin outlives the role that created it, so this is checked here AND
        # on every read -- a member demoted from owner must not keep a pinned
        # sentiment chart working.
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
    """Last sync per connector — the panel that makes the rest trustworthy.

    A number is worthless if nobody can tell whether it is current, and
    ``needs_reauth`` is reported separately from an old date because auto-sync
    skips a dead token entirely: waiting will never fix it.
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
