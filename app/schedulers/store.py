"""CRUD over the ``schedulers`` table.

Every read/write pairs ``org_id`` with ``user_id``: a scheduler belongs to
the person who created it, so one member can never list, edit, or delete
another's — even inside the same org. That is stricter than the org-wide
scoping used for policy content, deliberately: a scheduler carries a
personal free-text prompt and mails to one address.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..db.connection import get_connection

# Phase 1 supports the two services that already have a real "activity since
# timestamp T" primitive (GitHub ``list_commits(since=)``, Slack
# ``conversations.history(oldest=)``). Notion/Linear/Drive need new fetch
# logic before they can be added here.
SUPPORTED_PROVIDERS = ("github", "slack")
FREQUENCIES = ("weekly", "monthly")

# Postgres interval per frequency — used both to seed ``next_run_at`` at
# creation and to advance it after a successful run, so the two can never
# drift apart.
_FREQUENCY_INTERVAL = {"weekly": "7 days", "monthly": "1 month"}


class SchedulerError(Exception):
    """Invalid scheduler input (unsupported provider or frequency)."""


@dataclass(frozen=True)
class Scheduler:
    """A row of ``schedulers``."""

    id: str
    org_id: str
    user_id: str
    connection_id: str
    provider: str
    frequency: str
    prompt: str
    status: str  # active | failed
    last_run_at: datetime | None
    next_run_at: datetime
    attempts: int
    last_error: str | None
    created_at: datetime


COLUMNS = (
    "id::text, org_id::text, user_id::text, connection_id::text, provider, "
    "frequency, prompt, status, last_run_at, next_run_at, attempts, "
    "last_error, created_at"
)


def row_to_scheduler(row) -> Scheduler:
    return Scheduler(
        id=row[0],
        org_id=row[1],
        user_id=row[2],
        connection_id=row[3],
        provider=row[4],
        frequency=row[5],
        prompt=row[6],
        status=row[7],
        last_run_at=row[8],
        next_run_at=row[9],
        attempts=row[10] or 0,
        last_error=row[11],
        created_at=row[12],
    )


def interval_for(frequency: str) -> str:
    """Postgres interval string for a frequency. Raises on an unknown one."""
    try:
        return _FREQUENCY_INTERVAL[frequency]
    except KeyError:
        raise SchedulerError(
            f"Unsupported frequency {frequency!r} — expected one of {FREQUENCIES}."
        ) from None


def create_scheduler(
    org_id: str,
    user_id: str,
    connection_id: str,
    provider: str,
    frequency: str,
    prompt: str,
) -> Scheduler:
    """Create a scheduler, first run one full interval from now.

    ``next_run_at = now() + interval`` rather than ``now()``: the first
    report should cover a real period of activity, not fire immediately with
    an empty window.

    ``provider``/``frequency`` are validated here, not only at the API edge,
    because the chat-setup flow fills them from an LLM tool call — untrusted
    input that must not reach the table unchecked.
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise SchedulerError(
            f"Unsupported provider {provider!r} — expected one of {SUPPORTED_PROVIDERS}."
        )
    interval = interval_for(frequency)
    if not prompt.strip():
        raise SchedulerError("A scheduler needs a non-empty prompt.")

    with get_connection() as conn:
        row = conn.execute(
            f"""
            INSERT INTO schedulers
                (org_id, user_id, connection_id, provider, frequency, prompt,
                 next_run_at)
            VALUES (%s, %s, %s, %s, %s, %s, now() + %s::interval)
            RETURNING {COLUMNS}
            """,
            (org_id, user_id, connection_id, provider, frequency, prompt, interval),
        ).fetchone()
    return row_to_scheduler(row)


def list_schedulers(org_id: str, user_id: str) -> list[Scheduler]:
    """This user's own schedulers, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM schedulers "
            "WHERE org_id = %s AND user_id = %s ORDER BY created_at DESC",
            (org_id, user_id),
        ).fetchall()
    return [row_to_scheduler(row) for row in rows]


def get_scheduler(org_id: str, user_id: str, scheduler_id: str) -> Scheduler | None:
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {COLUMNS} FROM schedulers "
            "WHERE id = %s AND org_id = %s AND user_id = %s",
            (scheduler_id, org_id, user_id),
        ).fetchone()
    return row_to_scheduler(row) if row else None


def update_scheduler(
    org_id: str,
    user_id: str,
    scheduler_id: str,
    *,
    frequency: str | None = None,
    prompt: str | None = None,
) -> Scheduler | None:
    """Edit a scheduler's cadence and/or prompt. Returns None if not theirs.

    Changing the frequency re-bases ``next_run_at`` off the last run (or
    creation, for one that has never run), so switching weekly→monthly does
    not leave a run scheduled on the old cadence.
    """
    sets: list[str] = []
    params: list[object] = []
    if prompt is not None:
        if not prompt.strip():
            raise SchedulerError("A scheduler needs a non-empty prompt.")
        sets.append("prompt = %s")
        params.append(prompt)
    if frequency is not None:
        interval = interval_for(frequency)
        sets.append("frequency = %s")
        params.append(frequency)
        sets.append("next_run_at = coalesce(last_run_at, created_at) + %s::interval")
        params.append(interval)
    if not sets:
        return get_scheduler(org_id, user_id, scheduler_id)

    params.extend([scheduler_id, org_id, user_id])
    with get_connection() as conn:
        row = conn.execute(
            f"UPDATE schedulers SET {', '.join(sets)} "
            f"WHERE id = %s AND org_id = %s AND user_id = %s RETURNING {COLUMNS}",
            tuple(params),
        ).fetchone()
    return row_to_scheduler(row) if row else None


def delete_scheduler(org_id: str, user_id: str, scheduler_id: str) -> bool:
    """Delete one of this user's schedulers. False if it isn't theirs."""
    with get_connection() as conn:
        row = conn.execute(
            "DELETE FROM schedulers WHERE id = %s AND org_id = %s AND user_id = %s "
            "RETURNING id",
            (scheduler_id, org_id, user_id),
        ).fetchone()
    return row is not None
