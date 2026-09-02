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

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.exceptions import AuthError, ProviderError
from ..insights import panels as panel_defs
from ..insights import registry, scopes
from ..insights import store as insight_store
from ..workspaces.store import assert_member
from .deps import SessionClaims, get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/insights", tags=["insights"])

#: How far back a dashboard looks, per period. Not the same as the bucket size:
#: a weekly view of one week is a single bar, which tells nobody anything.
_WINDOW_DAYS = {"week": 84, "month": 365, "quarter": 730}


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
        else [{"bucket": p.bucket, "group": p.group, "value": p.value} for p in points],
        # Facts only exist from the first sync after this shipped, and author
        # names cannot be backfilled at all. Without this the axis silently
        # starts on deploy day and reads as if nobody worked before it.
        "measured_since": begun.isoformat() if begun else None,
    }


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
