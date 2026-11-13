"""Linear charts: throughput, states, load, cycle time.

State, assignee and team live in chunk prose, so this reads the adapter's
structured issue feed and records what is countable. Rides the ingest job,
not the sync tick: the ingest stamp would make the connection not-due on the
tick, and the job already holds a built adapter.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from ..db.connection import get_connection

logger = logging.getLogger(__name__)

PROVIDER = "linear"

#: How far back a sync reads. A first sync against a years-old workspace must
#: not pull every issue ever filed into one request, and the adapter's own
#: ``max_issues`` bounds it again on top of this.
WINDOW_DAYS = 180

#: Every issue that moved, whatever its state -- this is what the funnel and
#: the aging chart count.
KIND_STATE = "issue_state"
#: Only the ones that actually finished. Separate kind rather than a filter on
#: state, so "completed" is decided once, here, instead of in every query.
KIND_COMPLETED = "issue_completed"

#: Linear's own lifecycle categories. Only ``completed`` is success (canceled
#: is not finishing), and never by state NAME, which teams rename.
_COMPLETED_TYPE = "completed"
#: The attrs key the state's type is kept under (see `_issue_attrs`).
STATE_TYPE_ATTR = "state_type"


def record_linear_facts(org_id: str, *, workspace_id: str | None, adapter) -> int:
    """Record countable issue facts for this connection. Returns rows written.
    Never raises: the job already succeeded, and raising would make it retry."""
    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)

    try:
        issues = adapter.fetch_recent_issues(since)
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning(
            "insights: could not read Linear issues for org %s (workspace=%s)",
            org_id, workspace_id, exc_info=True,
        )
        return 0

    rows: list[tuple] = []
    for issue in issues or []:
        rows.extend(_issue_rows(org_id, workspace_id, issue))

    written = _write(rows, workspace_id)
    logger.info(
        "insights: recorded %s Linear facts for org %s (workspace=%s)",
        written, org_id, workspace_id,
    )
    return written


def _issue_rows(org_id, workspace_id, issue) -> list[tuple]:
    """One state row always, plus a completion row when it finished. Keyed by
    the issue identifier, so a later sync UPDATES the rows, never doubles them."""
    identifier = (issue.get("identifier") or "").strip()
    if not identifier:
        return []

    state = issue.get("state") or ""
    state_type = issue.get("state_type") or ""
    assignee = (issue.get("assignee") or "").strip() or None
    # `subject` carries the TEAM, so grouping by subject is "by team" for every
    # Linear metric -- the same slot repos occupy for GitHub.
    team = (issue.get("team") or "").strip() or None
    moved_at = issue.get("at")
    created_at = issue.get("created_at")
    completed_at = issue.get("completed_at")

    attrs = _issue_attrs(issue)
    rows = [(
        org_id, workspace_id, PROVIDER, KIND_STATE,
        assignee, team, state,
        moved_at or completed_at or created_at, None,
        issue.get("url") or None, identifier, attrs,
    )]

    if state_type == _COMPLETED_TYPE:
        # Linear leaves `completedAt` empty on some older issues. The
        # completion is real, so it still counts -- dated by when it last
        # moved, which is the closest honest answer available.
        when = completed_at or moved_at
        cycle = (
            (when - created_at).total_seconds()
            if when and created_at else None
        )
        # A missing date stays None rather than becoming 0: a zero drags a
        # median toward "instant", which is a claim about speed nobody made.
        rows.append((
            org_id, workspace_id, PROVIDER, KIND_COMPLETED,
            assignee, team, state,
            when, cycle,
            issue.get("url") or None, identifier, attrs,
        ))

    return rows


def _issue_attrs(issue: dict) -> Jsonb:
    """Every simple field of the issue (`insights.fields`) plus ``state_type``,
    Linear's OWN state category, which decides "open"/"closed"
    (`store.state_types`), never a list of state names."""
    fields = dict(issue.get("fields")) if isinstance(issue.get("fields"), dict) else {}
    state_type = (issue.get("state_type") or "").strip().lower()
    if state_type:
        fields[STATE_TYPE_ATTR] = state_type
    return Jsonb(fields)

def _write(rows: list[tuple], workspace_id: str | None) -> int:
    """Upsert every row in one statement. The conflict target must match the
    scope's PARTIAL unique index (NULLs are distinct in a plain UNIQUE)."""
    if not rows:
        return 0

    if workspace_id is None:
        conflict = """
            ON CONFLICT (org_id, provider, kind, external_id)
                WHERE workspace_id IS NULL AND external_id IS NOT NULL
        """
    else:
        conflict = """
            ON CONFLICT (org_id, workspace_id, provider, kind, external_id)
                WHERE workspace_id IS NOT NULL AND external_id IS NOT NULL
        """

    sql = f"""
        INSERT INTO activity_facts
            (org_id, workspace_id, provider, kind, actor, subject, state,
             occurred_at, value, url, external_id, attrs)
        VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        {conflict}
        DO UPDATE SET actor       = EXCLUDED.actor,
                      attrs       = EXCLUDED.attrs,
                      subject     = EXCLUDED.subject,
                      state       = EXCLUDED.state,
                      occurred_at = EXCLUDED.occurred_at,
                      value       = EXCLUDED.value,
                      url         = EXCLUDED.url
    """

    try:
        with get_connection() as conn:
            conn.cursor().executemany(sql, rows)
            conn.commit()
    except Exception:  # noqa: BLE001 - a stale chart, never a failed job
        logger.warning("insights: could not write Linear facts", exc_info=True)
        return 0
    return len(rows)
