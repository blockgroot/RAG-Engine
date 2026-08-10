"""GitHub installation uniqueness + choice helpers for Company vs space connect.

A Folio org may have many ``oauth_connections`` rows for GitHub (one org-wide
Company → Sources row, plus one per workspace). Each must bind a **different**
GitHub App ``installation_id``. Reusing one install on two rows means two
surfaces answer from the same repos — the "personal id linked in both places"
symptom — with no real isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db.connection import get_connection


@dataclass(frozen=True)
class GitHubInstallationConflict:
    """Another connection in this Folio org already uses this installation."""

    connection_id: str
    workspace_id: str | None  # None = Company → Sources (org-wide)


def find_github_installation_conflict(
    org_id: str,
    installation_id: str,
    *,
    for_workspace_id: str | None,
) -> GitHubInstallationConflict | None:
    """Return a conflict when ``installation_id`` is already bound elsewhere.

    Reconnecting the *same* surface (same ``for_workspace_id``, including
    ``None`` for org-wide) is allowed — that is how reconnect works. Binding
    the same install to a *different* surface is refused.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id::text, workspace_id::text
            FROM oauth_connections
            WHERE org_id = %s
              AND provider = 'github'
              AND source_config->>'installation_id' = %s
            """,
            (org_id, str(installation_id)),
        ).fetchall()
    for connection_id, workspace_id in rows:
        if workspace_id == for_workspace_id or (
            workspace_id is None and for_workspace_id is None
        ):
            continue
        return GitHubInstallationConflict(
            connection_id=connection_id,
            workspace_id=workspace_id,
        )
    return None


def summarize_installation(raw: dict) -> dict:
    """Shape a GitHub ``/user/installations`` entry for the choose UI."""
    account = raw.get("account") or {}
    return {
        "id": str(raw.get("id")),
        "login": account.get("login") or "",
        "account_type": account.get("type") or "",
        "html_url": account.get("html_url"),
    }
