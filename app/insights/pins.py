"""Charts a member kept.

Personal, scoped ``(org_id, user_id)`` like ``schedulers`` and unlike every
other tenant table -- a pin is one person's shortcut. That is why this feature
has no sharing model, no approval step and no "publish" anywhere: the curated
dashboards exist because a connector exists, and everything else is private to
whoever made it.

Stores the SPEC, never the numbers. Re-running one is a single ``GROUP BY``,
and snapshotting counts would freeze a chart that is meant to stay current --
the opposite choice from ``scheduler_reports``, which snapshots precisely
because a report is a record of one moment.
"""

from __future__ import annotations

import logging

from ..core.exceptions import ProviderError
from ..db.connection import get_connection

logger = logging.getLogger(__name__)

#: A shortcut list, not a dashboard builder. Past this it stops being "the few
#: charts I check" and the page needs organising, which is a different feature.
MAX_PINS = 12


def list_pins(org_id: str, user_id: str) -> list[dict]:
    """This member's pins, newest first."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id::text, workspace_id::text, metric, group_by, period, title
                  FROM insight_pins
                 WHERE org_id = %s AND user_id = %s
                 ORDER BY created_at DESC
                 LIMIT %s
                """,
                (org_id, user_id, MAX_PINS),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("insights: could not list pins", cause=exc) from exc

    return [
        {
            "id": r[0],
            "scope": r[1],
            "metric": r[2],
            "group_by": r[3],
            "period": r[4],
            "title": r[5],
        }
        for r in rows
    ]


def create_pin(
    org_id: str,
    user_id: str,
    *,
    workspace_id: str | None,
    metric: str,
    group_by: str | None,
    period: str,
    title: str,
) -> str | None:
    """Pin a chart. Returns the id, or None when it was already pinned.

    Pinning twice is a no-op rather than an error: the member's intent
    ("I want this on my page") is already satisfied, and an error would read as
    a failure.
    """
    if workspace_id is None:
        conflict = """
            ON CONFLICT (org_id, user_id, metric, period, coalesce(group_by, ''))
                WHERE workspace_id IS NULL DO NOTHING
        """
    else:
        conflict = """
            ON CONFLICT (org_id, user_id, workspace_id, metric, period,
                         coalesce(group_by, ''))
                WHERE workspace_id IS NOT NULL DO NOTHING
        """

    try:
        with get_connection() as conn:
            row = conn.execute(
                f"""
                INSERT INTO insight_pins
                    (org_id, user_id, workspace_id, metric, group_by, period, title)
                VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s)
                {conflict}
                RETURNING id::text
                """,
                (org_id, user_id, workspace_id, metric, group_by, period, title),
            ).fetchone()
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("insights: could not pin that chart", cause=exc) from exc

    return row[0] if row else None


def delete_pin(org_id: str, user_id: str, pin_id: str) -> bool:
    """Remove one pin. The ``user_id`` predicate is what makes it personal --
    without it a member could unpin someone else's."""
    try:
        with get_connection() as conn:
            deleted = conn.execute(
                "DELETE FROM insight_pins "
                " WHERE id = %s::uuid AND org_id = %s AND user_id = %s",
                (pin_id, org_id, user_id),
            ).rowcount
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("insights: could not remove that pin", cause=exc) from exc
    return deleted > 0
