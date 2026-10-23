"""``act_runs``: a request paused until the person decides the cards it waits on.

A chain ("open a GitHub issue, then comment its link on the Linear issue")
cannot draft its later step before the earlier write has run: the link does
not exist yet. The model says so (``service.CONTINUE``), and its conversation
is kept here with the cards. When every card it waits on is decided, their
results are given to it and it drafts the next cards.

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

#: Times one request may pause and continue; a chain longer than this is said.
MAX_STEPS = 4

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
    #: The forms already drafted in this request (``service._form_key``): never drafted twice.
    drafted: list[str]

    def source_list(self) -> list[Source]:
        return [Source(s["document_id"], s["title"], s["public"], tuple(s.get("viewers") or ()), s.get("uri"))
                for s in self.sources]


def _sources(sources: list[Source]) -> list[dict[str, Any]]:
    # Whole, unlike ``Source.as_dict``: the continued drafts are checked
    # against these viewers exactly as the first ones were.
    return [{"document_id": s.document_id, "title": s.title, "public": s.public,
             "viewers": list(s.viewers), "uri": s.uri} for s in sources]


def create(*, org_id: str, workspace_id: str | None, user_id: str, surface: dict, question: str,
           status: str,
           model: str | None, messages: list[dict], found: list[str], sources: list[Source], drafted: list[str],
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
             Json(_sources(sources)), step, Json(drafted), expires_at, status),
        ).fetchone()[0]
        conn.execute("UPDATE actions SET run_id = %s::uuid WHERE org_id = %s::uuid AND user_id = %s::uuid "
                     "AND id = ANY(%s::uuid[])", (run_id, org_id, user_id, action_ids))
    return run_id


def resume(org_id: str, user_id: str, action_id: str) -> tuple[Run, list[tuple[str, dict]]] | None:
    """The run this card belongs to, claimed for continuing, with every card it
    waited on as ``(label key, result)``; or ``None`` when there is nothing to
    continue: no run, a card still pending, expired, or another approval
    already continuing it."""
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
              AND r.expires_at > now()
              AND NOT EXISTS (SELECT 1 FROM actions a WHERE a.run_id = r.id
                              AND a.status IN ('proposed', 'executing'))
              -- standby too: a model that stopped after the first card without
              -- holding the next ("then post its link") still gets the results
              AND r.status IN ('waiting', 'standby')
            RETURNING {", ".join("r." + c for c in _COLUMNS.split(", "))}
            """,
            (action_id, org_id, user_id, org_id, user_id),
        ).fetchone()
        if row is None:
            return None
        cards = conn.execute(
            "SELECT action, status, result FROM actions WHERE run_id = %s::uuid AND org_id = %s::uuid "
            "ORDER BY created_at", (row[0], org_id),
        ).fetchall()
    return Run(*row), [(a, {"status": s, **(r or {})}) for a, s, r in cards]


def finish(org_id: str, run_id: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE act_runs SET status = 'done' WHERE id = %s::uuid AND org_id = %s::uuid",
                     (run_id, org_id))
