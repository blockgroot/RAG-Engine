"""``actions``: the approval queue and the audit log, one row per proposed write.

Every query is pinned to ``org_id``, and every person-facing one to
``user_id`` too: a proposal is its proposer's, and no one else can approve,
reject or even see it. ``claim`` is the single-execution guarantee -- one
conditional UPDATE that only one caller can win -- so a double click, two tabs
or a replayed request run a write at most once.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg.types.json import Json

from ..core.exceptions import (
    ActionConflict,
    ActionExpired,
    ActionRejected,
    NotFoundError,
)
from ..db.connection import get_connection
from .base import Proposal, Source
from .exposure import NEEDS_ACK, PUBLIC

#: Statuses a claimed write can end in.
FINAL_STATUSES = frozenset({"succeeded", "failed", "unknown"})

_COLUMNS = (
    "id::text, org_id::text, workspace_id::text, user_id::text, "
    "conversation_id::text, surface, action, target, payload, payload_hash, "
    "sources, exposure, acknowledged, status, expires_at, decided_at, "
    "executed_at, result, created_at, original_payload"
)


@dataclass(frozen=True)
class ActionRow:
    id: str
    org_id: str
    workspace_id: str | None
    user_id: str | None
    conversation_id: str | None
    surface: str
    action: str
    target: dict[str, Any]
    payload: dict[str, Any]
    payload_hash: str
    sources: list[dict[str, Any]]
    exposure: str
    acknowledged: bool
    status: str
    expires_at: datetime
    decided_at: datetime | None
    executed_at: datetime | None
    result: dict[str, Any] | None
    created_at: datetime
    #: The model's draft, set on the person's first edit; ``None`` if unedited.
    original_payload: dict[str, Any] | None = None


def _row(r) -> ActionRow:
    return ActionRow(*r)


def payload_hash(action: str, target: dict[str, Any], payload: dict[str, Any]) -> str:
    """A fingerprint of exactly what will run. The card sends it back on
    Approve; a mismatch means the person saw something else."""
    canonical = json.dumps(
        {"action": action, "target": target, "payload": payload},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def create(
    *,
    org_id: str,
    workspace_id: str | None,
    user_id: str,
    conversation_id: str | None,
    surface: str,
    proposal: Proposal,
    sources: tuple[Source, ...] | list[Source],
    exposure: str,
    ttl_seconds: int,
) -> ActionRow:
    """Store a proposal. A public exposure is refused here AND by the table's
    CHECK, so no caller can store one by forgetting to ask."""
    if exposure == PUBLIC:
        raise ActionRejected(
            "This would post content from private documents somewhere public."
        )
    target = proposal.target.as_dict()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    with get_connection() as conn:
        r = conn.execute(
            f"""
            INSERT INTO actions
                (org_id, workspace_id, user_id, conversation_id, surface, action,
                 target, payload, payload_hash, sources, exposure, expires_at)
            VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {_COLUMNS}
            """,
            (
                org_id, workspace_id, user_id, conversation_id, surface,
                proposal.action, Json(target), Json(proposal.payload),
                payload_hash(proposal.action, target, proposal.payload),
                Json([s.as_dict() for s in sources]), exposure, expires_at,
            ),
        ).fetchone()
    return _row(r)


def get(org_id: str, user_id: str, action_id: str) -> ActionRow | None:
    """The proposer's own row, or ``None`` -- for anyone else it does not exist."""
    with get_connection() as conn:
        r = conn.execute(
            f"SELECT {_COLUMNS} FROM actions "
            "WHERE id = %s::uuid AND org_id = %s::uuid AND user_id = %s::uuid",
            (action_id, org_id, user_id),
        ).fetchone()
    return _row(r) if r else None


def claim(
    org_id: str,
    user_id: str,
    action_id: str,
    *,
    shown_hash: str,
    acknowledged: bool = False,
) -> ActionRow:
    """Move a proposal to ``executing`` if, and only if, it may run now.

    All conditions sit in ONE UPDATE, so two concurrent approvals cannot both
    win. When nothing matched, the row is read back only to say why.
    """
    with get_connection() as conn:
        r = conn.execute(
            f"""
            UPDATE actions
               SET status = 'executing', decided_at = now(),
                   acknowledged = acknowledged OR %s
             WHERE id = %s::uuid AND org_id = %s::uuid AND user_id = %s::uuid
               AND status = 'proposed'
               AND expires_at > now()
               AND payload_hash = %s
               AND (exposure <> ALL(%s) OR acknowledged OR %s)
            RETURNING {_COLUMNS}
            """,
            (acknowledged, action_id, org_id, user_id, shown_hash,
             list(NEEDS_ACK), acknowledged),
        ).fetchone()
    if r:
        return _row(r)

    _why_not(org_id, user_id, action_id, shown_hash)
    raise ActionConflict(
        "This shares private content with people who can't see it. "
        "Tick the acknowledgement to approve."
    )


def _why_not(org_id: str, user_id: str, action_id: str, shown_hash: str) -> ActionRow:
    """After a guarded UPDATE matched nothing: raise for the first reason that
    applies, or return the row when only the acknowledgement was missing."""
    row = get(org_id, user_id, action_id)
    if row is None:
        raise NotFoundError("No such action.")
    if row.status != "proposed":
        raise ActionConflict(f"This action was already {row.status}.")
    if row.expires_at <= datetime.now(timezone.utc):
        raise ActionExpired("This approval has expired. Ask again to get a fresh one.")
    if row.payload_hash != shown_hash:
        raise ActionConflict("What you approved is not what is stored. Ask again.")
    return row


def update_payload(
    org_id: str, user_id: str, action_id: str, *, shown_hash: str, payload: dict[str, Any],
) -> ActionRow:
    """Replace a pending draft with the person's edit, in ONE guarded UPDATE.

    Same conditions as ``claim`` (the proposer, still proposed, not expired,
    the version they saw), so an edit can never race an approval into
    sending something unseen. The target, sources, exposure and expiry stay
    as they were; the model's first draft is kept in ``original_payload``.
    """
    row = get(org_id, user_id, action_id)
    if row is None:
        raise NotFoundError("No such action.")
    new_hash = payload_hash(row.action, row.target, payload)
    with get_connection() as conn:
        r = conn.execute(
            f"""
            UPDATE actions
               SET payload = %s, payload_hash = %s,
                   original_payload = COALESCE(original_payload, payload)
             WHERE id = %s::uuid AND org_id = %s::uuid AND user_id = %s::uuid
               AND status = 'proposed' AND expires_at > now() AND payload_hash = %s
            RETURNING {_COLUMNS}
            """,
            (Json(payload), new_hash, action_id, org_id, user_id, shown_hash),
        ).fetchone()
    if r:
        return _row(r)
    _why_not(org_id, user_id, action_id, shown_hash)
    raise ActionConflict("This draft changed. Reload it and try again.")


def finish(org_id: str, action_id: str, *, status: str, result: dict[str, Any]) -> None:
    """Record the outcome of a claimed write. Only ``executing`` rows move, so
    an outcome can never be written twice or onto an unapproved proposal."""
    if status not in FINAL_STATUSES:
        raise ValueError(f"not a final status: {status!r}")
    with get_connection() as conn:
        conn.execute(
            "UPDATE actions SET status = %s, result = %s, executed_at = now() "
            "WHERE id = %s::uuid AND org_id = %s::uuid AND status = 'executing'",
            (status, Json(result), action_id, org_id),
        )


def reject(org_id: str, user_id: str, action_id: str) -> bool:
    """Cancel a proposal. Returns whether there was one to cancel."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE actions SET status = 'rejected', decided_at = now() "
            "WHERE id = %s::uuid AND org_id = %s::uuid AND user_id = %s::uuid "
            "AND status = 'proposed'",
            (action_id, org_id, user_id),
        )
        return (cur.rowcount or 0) > 0


def expire_stale(limit: int = 500) -> int:
    """Mark timed-out proposals ``expired``. Bounded by a CTE (a ``LIMIT`` in
    an ``IN`` subquery does not bound an UPDATE). ``claim`` already refuses
    them by time; this keeps the log honest."""
    with get_connection() as conn:
        cur = conn.execute(
            """
            WITH stale AS (
                SELECT id FROM actions
                 WHERE status = 'proposed' AND expires_at <= now()
                 ORDER BY expires_at
                 LIMIT %s
                 FOR UPDATE SKIP LOCKED
            )
            UPDATE actions a SET status = 'expired'
              FROM stale WHERE a.id = stale.id
            """,
            (limit,),
        )
        return cur.rowcount or 0


def executed_since(org_id: str, user_id: str, since: datetime) -> int:
    """How many writes this person has sent since ``since``, for the rate cap.
    Counted from the log itself, so the cap and the record cannot disagree."""
    with get_connection() as conn:
        r = conn.execute(
            "SELECT count(*) FROM actions WHERE org_id = %s::uuid AND user_id = %s::uuid "
            "AND decided_at >= %s AND status IN ('executing', 'succeeded', 'failed', 'unknown')",
            (org_id, user_id, since),
        ).fetchone()
    return int(r[0])


def list_for_org(org_id: str, *, user_id: str | None = None, limit: int = 50) -> list[ActionRow]:
    """Recent actions, newest first, optionally one person's (their Act page)."""
    limit = max(1, min(limit, 200))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM actions WHERE org_id = %s::uuid "
            "AND (%s::uuid IS NULL OR user_id = %s::uuid) "
            "ORDER BY created_at DESC LIMIT %s",
            (org_id, user_id, user_id, limit),
        ).fetchall()
    return [_row(r) for r in rows]
