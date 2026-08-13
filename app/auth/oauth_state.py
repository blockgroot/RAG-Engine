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


def create_state(org_id: str, provider: str, workspace_id: str | None = None) -> str:
    """Create a state value for a connect flow.

    ``workspace_id`` (Workspace-within-a-Workspace): ``None`` (default) is
    today's org-wide admin connect flow, unchanged. Non-``None`` records
    which sub-workspace this personal connection is for, so the callback
    knows to save it scoped to that workspace instead of the org.
    """
    state = secrets.token_urlsafe(_STATE_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_STATE_TTL_MINUTES)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO oauth_states (state, org_id, provider, expires_at, workspace_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (state, org_id, provider, expires_at, workspace_id),
        )
    return state


def consume_state(state: str, *, provider: str) -> tuple[str, str | None]:
    """Validate + consume a state value, scoped to ``provider``.

    Returns ``(org_id, workspace_id)`` — ``workspace_id`` is ``None`` for the
    org-wide connect flow. Raises ``OAuthError`` if the state is unknown,
    expired, already used, or was issued for a different provider (defends
    against a state value generated for one connect flow being replayed
    against another).
    """
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE oauth_states SET consumed_at = now() "
            "WHERE state = %s AND provider = %s AND consumed_at IS NULL AND expires_at > now() "
            "RETURNING org_id::text, workspace_id::text",
            (state, provider),
        ).fetchone()
    if not row:
        raise OAuthError("Invalid, expired, or already-used OAuth state")
    return row[0], row[1]


def peek_state_workspace(state: str, *, provider: str) -> str | None:
    """Workspace id recorded on ``state``, WITHOUT consuming or validating it.

    Navigation only — it exists so a failed/incomplete connect can land the user
    back on the page they started from instead of a generic one. Deliberately
    NOT an authorization primitive:

    * it never consumes the state, so it cannot be used in place of
      ``consume_state`` to complete a flow;
    * it reads expired and already-consumed rows on purpose, because by
      definition it runs after something went wrong;
    * it returns only a ``workspace_id`` — never an ``org_id`` — so no caller can
      accidentally use its result to scope a write. Anything that binds a
      credential must go through ``consume_state``.

    Returns ``None`` for an unknown state or an org-wide flow (which are the same
    thing as far as picking a redirect goes).
    """
    if not state:
        return None
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT workspace_id::text FROM oauth_states "
                "WHERE state = %s AND provider = %s",
                (state, provider),
            ).fetchone()
    except Exception:  # noqa: BLE001 - a nicer redirect must never raise
        return None
    return row[0] if row else None
