"""``live_tool_calls``: one row per live read (plan D14).

Never the token, never the result text. Best-effort by construction: an audit
failure is logged and swallowed, because a missing row is a gap in a log and a
failed answer is a failed answer.
"""

from __future__ import annotations

import logging

from ..db.connection import get_connection
from .base import LiveRead
from .context import LiveRequest

logger = logging.getLogger("livetools")

#: Days a row is kept; swept on the external tick.
RETENTION_DAYS = 90


def record(request: LiveRequest, read: LiveRead, *, mode: str, latency_ms: int | None) -> None:
    logger.info(
        "livetools.read provider=%s outcome=%s truncated=%s latency_ms=%s",
        read.provider, read.outcome, read.truncated, latency_ms,
    )
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO live_tool_calls
                    (org_id, workspace_id, user_id, conversation_id, provider,
                     external_id, mode, outcome, truncated, latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    request.org_id, request.workspace_id, request.user_id,
                    request.conversation_id, read.provider, read.external_id,
                    mode, read.outcome, read.truncated, latency_ms,
                ),
            )
    except Exception:  # noqa: BLE001 - an unaudited read, never a failed answer
        logger.warning("live_tool_calls insert failed", exc_info=True)


def purge_expired() -> int:
    """Drop rows past ``RETENTION_DAYS``. Returns how many."""
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM live_tool_calls WHERE created_at < now() - make_interval(days => %s)",
            (RETENTION_DAYS,),
        )
        return cur.rowcount or 0
