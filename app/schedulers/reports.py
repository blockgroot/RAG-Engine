"""Stored reports: an immutable record of each run, for "Your reports".

Prompt, provider, cadence and space name are snapshotted, so editing a
scheduler or renaming a space never rewrites history. Scoped by
``(org_id, user_id)``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime

from ..db.connection import get_connection

COLUMNS = (
    "id::text, scheduler_id::text, org_id::text, user_id::text, provider, "
    "frequency, prompt, space_name, report_text, items, notes, "
    "window_start, window_end, delivered_to, created_at"
)


@dataclass(frozen=True)
class Report:
    """One generated report."""

    id: str
    scheduler_id: str | None
    org_id: str
    user_id: str
    provider: str
    frequency: str
    prompt: str
    space_name: str | None
    report_text: str
    #: ``[{"summary": …, "url": …}]`` — the activity the report was built
    #: from. Links live here, never in ``report_text``: the model is never
    #: asked to write a URL, so a page or email renders them from this.
    items: list[dict]
    notes: list[str]
    window_start: datetime
    window_end: datetime
    #: The address the mail was accepted for, or None if delivery failed. The
    #: report itself is still readable — that is the point of storing it.
    delivered_to: str | None
    created_at: datetime
    #: How many reports this schedule has in all, set by ``list_reports`` so a
    #: list that shows only the latest runs can say how many it left out.
    runs_total: int | None = None


def _loads(value) -> list:
    """JSONB comes back parsed; a text column or a fake DB may not."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return list(value or [])


def row_to_report(row) -> Report:
    return Report(
        id=row[0],
        scheduler_id=row[1],
        org_id=row[2],
        user_id=row[3],
        provider=row[4],
        frequency=row[5],
        prompt=row[6],
        space_name=row[7],
        report_text=row[8],
        items=_loads(row[9]),
        notes=_loads(row[10]),
        window_start=row[11],
        window_end=row[12],
        delivered_to=row[13],
        created_at=row[14],
    )


def save_report(
    *,
    scheduler_id: str,
    org_id: str,
    user_id: str,
    provider: str,
    frequency: str,
    prompt: str,
    space_name: str | None,
    report_text: str,
    items: list[dict],
    notes: list[str],
    window_start: datetime,
    window_end: datetime,
) -> Report:
    """Persist one run's output, before the email, so a mail failure loses
    only the notification. ``mark_delivered`` stamps an accepted send.
    """
    with get_connection() as conn:
        row = conn.execute(
            f"""
            INSERT INTO scheduler_reports
                (scheduler_id, org_id, user_id, provider, frequency, prompt,
                 space_name, report_text, items, notes, window_start,
                 window_end)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s,
                    %s)
            RETURNING {COLUMNS}
            """,
            (
                scheduler_id,
                org_id,
                user_id,
                provider,
                frequency,
                prompt,
                space_name,
                report_text,
                json.dumps(items),
                json.dumps(notes),
                window_start,
                window_end,
            ),
        ).fetchone()
    return row_to_report(row)


def mark_delivered(report_id: str, to: str) -> None:
    """Record that the mail was accepted for ``to``."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE scheduler_reports SET delivered_to = %s WHERE id = %s",
            (to, report_id),
        )


#: Latest reports listed per schedule. Per schedule, not overall: a cap of 50
#: in all let five daily schedules push a weekly one's history off the page.
PER_SCHEDULE = 12


def list_reports(
    org_id: str, user_id: str, per_schedule: int = PER_SCHEDULE, limit: int = 300
) -> list[Report]:
    """This person's reports, newest first: each schedule's latest ``per_schedule``.

    Bounded per schedule and by ``limit``; ``runs_total`` tells the page when
    older ones are hidden. An orphaned report is its own group.
    """
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {COLUMNS}, runs_total FROM ("
            f"  SELECT {COLUMNS},"
            "   row_number() OVER w AS rn,"
            "   count(*) OVER (PARTITION BY COALESCE(scheduler_id::text, id::text)) AS runs_total"
            "  FROM scheduler_reports"
            "  WHERE org_id = %s AND user_id = %s"
            "  WINDOW w AS (PARTITION BY COALESCE(scheduler_id::text, id::text) ORDER BY created_at DESC)"
            ") latest WHERE rn <= %s "
            "ORDER BY created_at DESC LIMIT %s",
            (org_id, user_id, max(1, per_schedule), max(1, min(limit, 1000))),
        ).fetchall()
    return [replace(row_to_report(row), runs_total=row[15]) for row in rows]


def get_report(org_id: str, user_id: str, report_id: str) -> Report | None:
    """One report, or None when it is not this person's (scoped in the WHERE)."""
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {COLUMNS} FROM scheduler_reports "
            "WHERE id = %s AND org_id = %s AND user_id = %s",
            (report_id, org_id, user_id),
        ).fetchone()
    return row_to_report(row) if row else None
