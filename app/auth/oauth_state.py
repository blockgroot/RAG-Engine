"""Server-side OAuth ``state`` storage (Phase 13) — CSRF/replay protection.

A signed-JWT-only ``state`` can still be replayed (anyone who intercepts the
redirect URL can reuse it while it's valid). Storing it server-side and
consuming it atomically on lookup — the same single-use pattern as
``magic_link.py`` — closes that: a captured callback URL is useless a second
time. Scoped to the admin's ``org_id`` at issue time, so the callback resolves
the correct tenant without trusting anything the client supplies.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from ..core.exceptions import OAuthError
from ..db.connection import get_connection

_STATE_TTL_MINUTES = 10
_STATE_BYTES = 32


def create_state(org_id: str, provider: str) -> str:
    state = secrets.token_urlsafe(_STATE_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_STATE_TTL_MINUTES)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO oauth_states (state, org_id, provider, expires_at) "
            "VALUES (%s, %s, %s, %s)",
            (state, org_id, provider, expires_at),
        )
    return state


def consume_state(state: str, *, provider: str) -> str:
    """Validate + consume a state value, scoped to ``provider``. Returns org_id.

    Raises ``OAuthError`` if the state is unknown, expired, already used, or
    was issued for a different provider (defends against a state value
    generated for one connect flow being replayed against another).
    """
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE oauth_states SET consumed_at = now() "
            "WHERE state = %s AND provider = %s AND consumed_at IS NULL AND expires_at > now() "
            "RETURNING org_id::text",
            (state, provider),
        ).fetchone()
    if not row:
        raise OAuthError("Invalid, expired, or already-used OAuth state")
    return row[0]
