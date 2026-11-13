"""What a member may chart, and how fresh each connector is.

Membership is the security boundary: a join against ``workspace_members``,
never a filter applied afterwards. Not ``api/schedulers._connected_providers``:
the freshness panel needs ``last_sync_at`` and ``needs_reauth``, which that omits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core.exceptions import AuthError, ProviderError
from ..db.connection import get_connection
from ..workspaces.store import assert_member

#: How far back a dashboard / Ask chart looks, per period. Not the same as
#: the bucket size: a weekly view of one week is a single bar.
WINDOW_DAYS = {"day": 45, "week": 84, "month": 365, "quarter": 730}


def may_see_metric(metric, *, role: str, workspace_id: str | None, org_id: str, user_id: str) -> bool:
    """Whether this caller may see an ``owners_only`` metric: an org admin
    anywhere, a space owner in their space. The caller checks membership."""
    if not metric.owners_only:
        return True
    if role == "admin":
        return True
    if workspace_id is None:
        return False
    try:
        return assert_member(workspace_id, org_id, user_id) == "owner"
    except AuthError:
        return False


@dataclass(frozen=True)
class Scope:
    """One thing the scope picker can offer. ``id`` is ``None`` for the
    company, as ``workspace_id IS NULL`` everywhere else."""

    id: str | None
    name: str
    providers: list[str]


@dataclass(frozen=True)
class Freshness:
    """One connector's currency. ``needs_reauth`` is separate: auto-sync skips a
    dead token, so "last synced 6 days ago" would invite waiting forever."""

    provider: str
    last_sync_at: datetime | None
    needs_reauth: bool


def member_scopes(org_id: str, user_id: str) -> list[Scope]:
    """The company, plus every space this member belongs to. A space with
    nothing connected is kept (empty ``providers``) so it does not vanish."""
    try:
        with get_connection() as conn:
            org_providers = [
                row[0] for row in conn.execute(
                    """
                    SELECT DISTINCT provider FROM oauth_connections
                     WHERE org_id = %s AND workspace_id IS NULL
                     ORDER BY provider
                    """,
                    (org_id,),
                ).fetchall()
            ]
            # The join is the boundary: a space this user is not a member of
            # produces no row at all.
            rows = conn.execute(
                """
                SELECT w.id::text, w.name, c.provider
                  FROM workspaces w
                  JOIN workspace_members wm
                    ON wm.workspace_id = w.id AND wm.user_id = %s
                  LEFT JOIN oauth_connections c ON c.workspace_id = w.id
                 WHERE w.org_id = %s
                 ORDER BY w.name, c.provider
                """,
                (user_id, org_id),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("insights: could not resolve scopes", cause=exc) from exc

    spaces: dict[str, Scope] = {}
    for space_id, name, provider in rows:
        scope = spaces.setdefault(space_id, Scope(space_id, name, []))
        if provider and provider not in scope.providers:
            scope.providers.append(provider)

    return [Scope(None, "The company", org_providers), *spaces.values()]


def freshness(
    org_id: str, *, user_id: str, workspace_id: str | None
) -> list[Freshness]:
    """Last sync per connector in one scope. Raises ``AuthError`` for a space
    the member is not in: an empty list would claim "nothing connected"."""
    if workspace_id is not None:
        # The one place membership is validated. Do not inline a second check.
        assert_member(workspace_id, org_id, user_id)

    scope = (
        "AND workspace_id IS NULL" if workspace_id is None
        else "AND workspace_id = %(workspace_id)s"
    )
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT provider, last_sync_at, needs_reauth
                  FROM oauth_connections
                 WHERE org_id = %(org_id)s {scope}
                 ORDER BY provider
                """,
                {"org_id": org_id, "workspace_id": workspace_id},
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("insights: could not read freshness", cause=exc) from exc

    return [Freshness(r[0], r[1], bool(r[2])) for r in rows]
