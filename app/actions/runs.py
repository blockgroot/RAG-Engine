"""``act_runs``: a request paused at the cards it drafted.

Agent mode is a tool loop paused at each write ("open a GitHub issue, then
comment its link on the Linear issue"): the drafted calls stay unanswered,
the conversation is kept here with which card answers which call, and when
every card is decided each call is answered with what happened (the link,
the tool's error, a cancel) and the same conversation goes on
(``service.continue_run``).

Every query is pinned to ``org_id`` and ``user_id``: a run is its asker's.
``resume`` is one conditional UPDATE, so two approvals arriving together
continue the run once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg.types.json import Json

from ..db.connection import get_connection
from .base import Source

#: Times one request may pause for approval and go on.
MAX_STEPS = 6

_COLUMNS = ("id::text, org_id::text, workspace_id::text, user_id::text, surface, question, model, "
            "messages, found, sources, status, steps, drafted")


@dataclass(frozen=True)
class Run:
    id: str
    org_id: str
    workspace_id: str | None
    user_id: str
    surface: dict[str, Any]
    question: str
    model: str | None
    messages: list[dict[str, Any]]
    found: list[str]
    sources: list[dict[str, Any]]
    status: str
    steps: int
    #: ``{"keys": forms already drafted (never drafted twice), "calls": {card id: tool call id}}``.
    drafted: Any

    def source_list(self) -> list[Source]:
        return [Source(s["document_id"], s["title"], s["public"], tuple(s.get("viewers") or ()), s.get("uri"))
                for s in self.sources]


def _sources(sources: list[Source]) -> list[dict[str, Any]]:
    # Whole, unlike ``Source.as_dict``: the continued drafts are checked
    # against these viewers exactly as the first ones were.
    return [{"document_id": s.document_id, "title": s.title, "public": s.public,
             "viewers": list(s.viewers), "uri": s.uri} for s in sources]


def create(*, org_id: str, workspace_id: str | None, user_id: str, surface: dict, question: str,
           model: str | None, messages: list[dict], found: list[str], sources: list[Source], drafted: dict,
           action_ids: list[str], ttl_seconds: int, step: int = 1) -> str:
    """Store a paused run and tie the cards it waits on to it."""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    with get_connection() as conn:
        run_id = conn.execute(
            "INSERT INTO act_runs (org_id, workspace_id, user_id, surface, question, model, messages, "
            "found, sources, steps, drafted, expires_at, status) "
            "VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id::text",
            (org_id, workspace_id, user_id, Json(surface), question, model, Json(messages), Json(found),
             Json(_sources(sources)), step, Json(drafted), expires_at, "waiting"),
        ).fetchone()[0]
        conn.execute("UPDATE actions SET run_id = %s::uuid WHERE org_id = %s::uuid AND user_id = %s::uuid "
                     "AND id = ANY(%s::uuid[])", (run_id, org_id, user_id, action_ids))
    return run_id


def resume(org_id: str, user_id: str, action_id: str) -> tuple[Run, list[tuple[str, str, str, dict]]] | None:
    """The run this card belongs to, claimed for continuing, with every card
    of it as ``(id, action, status, result)``; or ``None`` when there is
    nothing to continue: no run, a card still pending, expired, another
    approval already continuing it, or every card cancelled (a cancel ends a
    request; going on drafted the cancelled card again)."""
    with get_connection() as conn:
        row = conn.execute(
            f"""
            WITH mine AS (
                SELECT run_id FROM actions
                WHERE id = %s::uuid AND org_id = %s::uuid AND user_id = %s::uuid AND run_id IS NOT NULL
            )
            UPDATE act_runs r SET status = 'resuming'
            FROM mine
            WHERE r.id = mine.run_id AND r.org_id = %s::uuid AND r.user_id = %s::uuid
              AND r.expires_at > now() AND r.status IN ('waiting', 'standby')
              AND NOT EXISTS (SELECT 1 FROM actions a WHERE a.run_id = r.id
                              AND a.status IN ('proposed', 'executing'))
              AND EXISTS (SELECT 1 FROM actions a WHERE a.run_id = r.id AND a.status <> 'rejected')
            RETURNING {", ".join("r." + c for c in _COLUMNS.split(", "))}
            """,
            (action_id, org_id, user_id, org_id, user_id),
        ).fetchone()
        if row is None:
            return None
        cards = conn.execute(
            "SELECT id::text, action, status, result FROM actions WHERE run_id = %s::uuid "
            "AND org_id = %s::uuid ORDER BY created_at", (row[0], org_id),
        ).fetchall()
    return Run(*row), [(i, a, s, r or {}) for i, a, s, r in cards]


def finish(org_id: str, run_id: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE act_runs SET status = 'done' WHERE id = %s::uuid AND org_id = %s::uuid",
                     (run_id, org_id))
