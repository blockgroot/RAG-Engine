"""Stored reports: what a run produced, kept for the "Your reports" page.

Split from ``store.py`` (which owns the *schedules*) because these are two
different lifetimes: a scheduler is mutable and personal — edit its prompt,
change its cadence — while a report is an immutable record of one run.

Everything a report displays is **snapshotted at generation time**, not
joined at read time: the prompt, provider, cadence and space name are copied
into the row. Re-resolving them would rewrite history the moment someone
edits their scheduler or renames a space, and a reader comparing last
month's report against this month's needs to see what was actually asked
then.

Scoped by ``(org_id, user_id)`` like the schedulers themselves — a report is
as personal as the schedule that produced it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
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
    """Persist one run's output. Called BEFORE the email is attempted.

    Order matters: the report exists first, so a mail failure costs a
    notification rather than the run's work. ``delivered_to`` is stamped
    afterwards by ``mark_delivered`` only if the send is accepted, which keeps
    "was this actually emailed?" answerable instead of assumed.
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


def list_reports(org_id: str, user_id: str, limit: int = 50) -> list[Report]:
    """This person's reports, newest first.

    Bounded rather than "all": a monthly scheduler running for two years is
    24 rows, but nothing stops someone keeping twenty schedulers, and an
    unbounded list is the kind of page that is fine until it isn't.
    """
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM scheduler_reports "
            "WHERE org_id = %s AND user_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (org_id, user_id, max(1, min(limit, 200))),
        ).fetchall()
    return [row_to_report(row) for row in rows]


def get_report(org_id: str, user_id: str, report_id: str) -> Report | None:
    """One report, or None when it is not this person's.

    Both scoping columns are in the WHERE clause, so a guessed id from
    another member (or another tenant) is indistinguishable from a deleted
    one — it simply does not exist.
    """
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {COLUMNS} FROM scheduler_reports "
            "WHERE id = %s AND org_id = %s AND user_id = %s",
            (report_id, org_id, user_id),
        ).fetchone()
    return row_to_report(row) if row else None
