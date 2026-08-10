"""Short-lived GitHub install-picker state (user OAuth → choose account).

After ``/auth/github/callback`` exchanges the code, we often know *who*
authorized but not *which App installation* should bind to Company Sources
vs a personal space. Auto-picking silently mixed those surfaces (same personal
install on both). This module parks the user token server-side so the
frontend can show every installation and the user picks one explicitly.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..core.exceptions import OAuthError
from ..db.connection import get_connection
from ..security import decrypt, encrypt

_PENDING_TTL_MINUTES = 10
_TOKEN_BYTES = 32


@dataclass(frozen=True)
class GitHubInstallPending:
    token: str
    org_id: str
    workspace_id: str | None
    access_token: str
    refresh_token: str | None
    token_expires_at: datetime | None


def create_github_install_pending(
    org_id: str,
    *,
    workspace_id: str | None,
    access_token: str,
    refresh_token: str | None = None,
    token_expires_at: datetime | None = None,
) -> str:
    """Store encrypted user tokens; return the opaque pending token for the UI."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_PENDING_TTL_MINUTES)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO github_install_pending (
                token, org_id, workspace_id,
                access_token_encrypted, refresh_token_encrypted,
                token_expires_at, expires_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                token,
                org_id,
                workspace_id,
                encrypt(access_token),
                encrypt(refresh_token) if refresh_token else None,
                token_expires_at,
                expires_at,
            ),
        )
    return token


def get_github_install_pending(token: str) -> GitHubInstallPending:
    """Peek at a pending row without consuming it (for the choose UI)."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT org_id::text, workspace_id::text,
                   access_token_encrypted, refresh_token_encrypted,
                   token_expires_at
            FROM github_install_pending
            WHERE token = %s AND consumed_at IS NULL AND expires_at > now()
            """,
            (token,),
        ).fetchone()
    if not row:
        raise OAuthError("Invalid, expired, or already-used GitHub install selection")
    return GitHubInstallPending(
        token=token,
        org_id=row[0],
        workspace_id=row[1],
        access_token=decrypt(row[2]),
        refresh_token=decrypt(row[3]) if row[3] else None,
        token_expires_at=row[4],
    )


def consume_github_install_pending(token: str) -> GitHubInstallPending:
    """Atomically consume the pending row (one-shot, like oauth_states)."""
    with get_connection() as conn:
        row = conn.execute(
            """
            UPDATE github_install_pending
            SET consumed_at = now()
            WHERE token = %s AND consumed_at IS NULL AND expires_at > now()
            RETURNING org_id::text, workspace_id::text,
                      access_token_encrypted, refresh_token_encrypted,
                      token_expires_at
            """,
            (token,),
        ).fetchone()
    if not row:
        raise OAuthError("Invalid, expired, or already-used GitHub install selection")
    return GitHubInstallPending(
        token=token,
        org_id=row[0],
        workspace_id=row[1],
        access_token=decrypt(row[2]),
        refresh_token=decrypt(row[3]) if row[3] else None,
        token_expires_at=row[4],
    )
