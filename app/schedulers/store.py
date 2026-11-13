"""CRUD over the ``schedulers`` table.

Every query pairs ``org_id`` with ``user_id``: a scheduler is personal (its
own prompt, one mailbox), so no other member may list, edit or delete it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..db.connection import get_connection

# Keep in step with ``activity._FETCHERS``: a provider without a fetcher fails
# every cycle. Drive is "google", as oauth_connections stores it.
SUPPORTED_PROVIDERS = ("github", "slack", "linear", "notion", "google")
# Daily works because autosync keeps the index at most an hour behind.
FREQUENCIES = ("daily", "weekly", "monthly")

# One table for seeding and advancing ``next_run_at``, so they never drift.
_FREQUENCY_INTERVAL = {"daily": "1 day", "weekly": "7 days", "monthly": "1 month"}


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
    #: NULL = org-wide; set = one space's own. Never used without org_id.
    workspace_id: str | None = None


COLUMNS = (
    "id::text, org_id::text, user_id::text, connection_id::text, "
    "workspace_id::text, provider, frequency, prompt, status, last_run_at, "
    "next_run_at, attempts, last_error, created_at"
)


def row_to_scheduler(row) -> Scheduler:
    return Scheduler(
        id=row[0],
        org_id=row[1],
        user_id=row[2],
        connection_id=row[3],
        workspace_id=row[4],
        provider=row[5],
        frequency=row[6],
        prompt=row[7],
        status=row[8],
        last_run_at=row[9],
        next_run_at=row[10],
        attempts=row[11] or 0,
        last_error=row[12],
        created_at=row[13],
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
    workspace_id: str | None = None,
) -> Scheduler:
    """Create a scheduler; its first run is one interval from now.

    ``workspace_id`` membership is checked at the API edge (``assert_member``).
    ``provider``/``frequency`` are checked here too: chat setup fills them
    from an LLM tool call.
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
                (org_id, user_id, connection_id, workspace_id, provider,
                 frequency, prompt, next_run_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now() + %s::interval)
            RETURNING {COLUMNS}
            """,
            (
                org_id,
                user_id,
                connection_id,
                workspace_id,
                provider,
                frequency,
                prompt,
                interval,
            ),
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
    """Edit cadence and/or prompt; None if not theirs. A new frequency
    re-bases ``next_run_at`` off the last run (or creation).
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
