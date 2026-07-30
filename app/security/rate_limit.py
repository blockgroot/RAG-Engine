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


def check_rate_limit(scope_key: str, *, settings: RateLimitSettings | None = None) -> None:
    """Increment the counter for ``scope_key``; raise 429 if over limit."""
    settings = settings or RateLimitSettings.from_env()
    if not settings.enabled:
        return

    now = datetime.now(timezone.utc)
    window = _window_start(now, settings.window_seconds)
    limit = settings.chat_requests_per_window

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
