"""Execute a registry metric as one scoped, parameterized aggregate.

The whole query is assembled from three sources and no others: a fixed
fragment from the registry, an identifier looked up in
``registry.DIMENSIONS``, and bound parameters. Nothing a caller typed ever
reaches the SQL text -- which matters more here than usual, because ``period``
and ``group_by`` are grammatically identifiers and so cannot be passed as %s.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core.exceptions import ProviderError
from ..db.connection import get_connection
from . import registry


@dataclass(frozen=True)
class Point:
    """One bar, one dot.

    ``bucket`` is the time bucket; ``group`` is the series within it (an
    actor, a repo, a state) and is ``None`` when the metric was not grouped.
    """

    bucket: str
    group: str | None
    value: float


def _scoped(sql_where: str, workspace_id: str | None) -> str:
    """The Workspace-within-a-Workspace predicate.

    ``workspace_id=None`` means org-wide and is NOT "any workspace": a space
    sees only its own rows and the org scope sees only org-wide ones. Written
    as a WHERE clause rather than a filter applied afterwards, so isolation
    never depends on the caller remembering to apply it.
    """
    return sql_where + (
        " AND workspace_id IS NULL" if workspace_id is None
        else " AND workspace_id = %(workspace_id)s"
    )


def run_metric(
    key: str,
    *,
    org_id: str,
    workspace_id: str | None,
    period: str,
    days: int = 90,
    group_by: str | None = None,
) -> list[Point]:
    """Count one registry metric in one scope over one window.

    Raises ``KeyError`` for an unknown metric and ``ValueError`` for an unknown
    period or dimension -- never a sanitized fallback. A chart drawn from a
    quietly corrected request is a chart nobody asked for.
    """
    metric = registry.get(key)

    if period not in registry.PERIODS:
        raise ValueError(
            f"unknown period {period!r}; expected one of {registry.PERIODS}"
        )
    if group_by is not None and group_by not in registry.DIMENSIONS:
        raise ValueError(f"unknown dimension {group_by!r}")

    # Both are looked-up constants by this point, never caller text.
    column = registry.DIMENSIONS[group_by] if group_by else None
    selected = f", {column}::text" if column else ", NULL::text"
    grouped = f", {column}" if column else ""

    where = _scoped(
        """
         WHERE org_id = %(org_id)s
           AND provider = %(provider)s
           AND kind = %(kind)s
           AND occurred_at >= now() - make_interval(days => %(days)s)
        """,
        workspace_id,
    )

    sql = f"""
        SELECT date_trunc('{period}', occurred_at) AS bucket{selected},
               {metric.select} AS value
          FROM activity_facts
          {where}
         GROUP BY bucket{grouped}
         ORDER BY bucket
    """

    params = {
        "org_id": org_id,
        "provider": metric.provider,
        "kind": metric.kind,
        "days": days,
        "workspace_id": workspace_id,
    }

    try:
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as exc:  # noqa: BLE001 - re-raised as our own type
        raise ProviderError(f"insights: metric {key} failed", cause=exc) from exc

    return [
        Point(bucket=row[0].isoformat(), group=row[1], value=float(row[2] or 0))
        for row in rows
    ]


def first_fact_at(
    provider: str, *, org_id: str, workspace_id: str | None
) -> datetime | None:
    """When measurement began for this provider in this scope.

    Facts only exist from the first sync after this feature deployed, and
    author names cannot be backfilled at all -- they were never captured. A
    chart whose axis silently starts on deploy day reads as if nobody worked
    before it, so the UI renders "measured since <this>". ``None`` means
    nothing has been recorded yet, which is a different statement from zero.
    """
    where = _scoped(
        " WHERE org_id = %(org_id)s AND provider = %(provider)s", workspace_id
    )
    try:
        with get_connection() as conn:
            row = conn.execute(
                f"SELECT min(occurred_at) FROM activity_facts {where}",
                {"org_id": org_id, "provider": provider,
                 "workspace_id": workspace_id},
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(
            f"insights: first_fact_at({provider}) failed", cause=exc
        ) from exc

    return row[0] if row else None
