"""Server-side OAuth ``state``: single-use, consumed atomically (CSRF/replay).

A signed-only state could be replayed from a captured redirect URL. The row
carries the issuing admin's ``org_id``, so the callback trusts nothing the
client sends.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from ..core.exceptions import OAuthError
from ..db.connection import get_connection

_STATE_TTL_MINUTES = 10
_STATE_BYTES = 32


def create_state(
    org_id: str,
    provider: str,
    workspace_id: str | None = None,
    *,
    user_id: str | None = None,
) -> str:
    """Create a state value for a connect flow.

    ``workspace_id`` ``None`` is org-wide; otherwise the connection is saved to
    that space. ``user_id`` binds person-scoped flows (``LINK_GITHUB``), read
    only by ``consume_link_state``.
    """
    state = secrets.token_urlsafe(_STATE_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_STATE_TTL_MINUTES)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO oauth_states (state, org_id, provider, expires_at, workspace_id, user_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (state, org_id, provider, expires_at, workspace_id, user_id),
        )
    return state


def consume_state(state: str, *, provider: str) -> tuple[str, str | None]:
    """Validate and consume a state for ``provider``; return ``(org_id, workspace_id)``.

    Raises ``OAuthError`` if unknown, expired, used, or issued for another
    provider (no replay across flows).
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
    """Workspace id on ``state``, without consuming or validating it.

    Navigation only (send a failed connect back where it started): reads expired
    and used rows on purpose and returns no ``org_id``, so it can never scope a
    write. Anything that binds a credential goes through ``consume_state``.
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


#: State ``provider`` for "Link your GitHub account". Distinct from ``"github"``
#: so a link state can never complete a connect flow, or the reverse.
LINK_GITHUB = "github_link"


def peek_state_provider(state: str) -> str | None:
    """The provider on ``state``, without consuming or validating it.

    GitHub has one callback URL, so it must tell link from connect first. It
    authorizes nothing: the chosen branch still consumes the state.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT provider FROM oauth_states WHERE state = %s", (state,)
        ).fetchone()
    return row[0] if row else None


def consume_link_state(state: str, *, provider: str) -> tuple[str, str]:
    """Consume a person-bound state; return ``(org_id, user_id)``.

    Also raises when the row has no user: a link must name the person who asked.
    """
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE oauth_states SET consumed_at = now() "
            "WHERE state = %s AND provider = %s AND consumed_at IS NULL AND expires_at > now() "
            "RETURNING org_id::text, user_id::text",
            (state, provider),
        ).fetchone()
    if not row or not row[1]:
        raise OAuthError("Invalid, expired, or already-used OAuth state")
    return row[0], row[1]
