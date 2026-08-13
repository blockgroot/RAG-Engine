"""Postgres-backed HTTP rate limiting (Phase 21).

Fixed-window counters keyed by an arbitrary scope string (e.g. ``org:<uuid>``).
No Redis required — reuses the existing connection pool. Intended for the chat
endpoint at minimum; callers choose scope + limit.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from ..config.settings import RateLimitSettings
from ..db.connection import get_connection


def _window_start(now: datetime, window_seconds: int) -> datetime:
    epoch = int(now.timestamp())
    start = epoch - (epoch % window_seconds)
    return datetime.fromtimestamp(start, tz=timezone.utc)


def check_rate_limit(
    scope_key: str,
    *,
    settings: RateLimitSettings | None = None,
    limit: int | None = None,
) -> None:
    """Increment the counter for ``scope_key``; raise 429 if over limit.

    ``limit`` overrides the default chat budget. It exists because the endpoints
    guarded here have genuinely different shapes: chat is authenticated and
    scoped per org, while the magic-link endpoint is anonymous and scoped per
    IP — so an office behind one NAT shares a single bucket there and needs a
    more generous allowance than one user's chat session.
    """
    settings = settings or RateLimitSettings.from_env()
    if not settings.enabled:
        return

    now = datetime.now(timezone.utc)
    window = _window_start(now, settings.window_seconds)
    limit = limit if limit is not None else settings.chat_requests_per_window

    with get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO api_rate_counters (scope_key, window_start, request_count)
            VALUES (%s, %s, 1)
            ON CONFLICT (scope_key, window_start) DO UPDATE
              SET request_count = api_rate_counters.request_count + 1
            RETURNING request_count
            """,
            (scope_key, window),
        ).fetchone()
    count = int(row[0])
    if count > limit:
        raise HTTPException(
            status_code=429,
            detail="Too many requests — please wait a moment and try again.",
        )


def prune_old_windows(
    *, settings: RateLimitSettings | None = None, limit: int = 10_000
) -> int:
    """Delete counters for windows that have already closed. Returns the count.

    Only the CURRENT window is ever read, so every past row is dead weight — but
    nothing deleted them, and the table grew one row per scope per window
    forever (a single user chatting for a year is ~525k rows). Bounded per sweep
    for the same reason as ``query_cache.prune_expired``: a neglected table must
    not turn a maintenance tick into a long lock-holding DELETE.

    Keeps one extra window of history so a sweep landing exactly on a boundary
    cannot delete the window a concurrent request is still incrementing.
    """
    settings = settings or RateLimitSettings.from_env()
    cutoff = _window_start(datetime.now(timezone.utc), settings.window_seconds)
    with get_connection() as conn:
        rows = conn.execute(
            """
            DELETE FROM api_rate_counters
            WHERE ctid IN (
                SELECT ctid FROM api_rate_counters
                WHERE window_start < %s - (%s || ' seconds')::interval
                LIMIT %s
            )
            RETURNING 1
            """,
            (cutoff, settings.window_seconds, limit),
        ).fetchall()
    return len(rows)
