"""What a member may chart, and how fresh each connector is.

Membership is the entire security boundary: a space the asker is not in must
never leave the database, because its charts would name colleagues and count
content they cannot read. That is a join against ``workspace_members``, not a
filter applied afterwards.

Deliberately NOT reusing ``api/schedulers._connected_providers``: that query
returns strictly less on purpose (no ``last_sync_at``, no ``needs_reauth``,
because a member picking a service does not need them). The freshness panel
needs exactly those two, so this is a different question, not a second copy of
the same one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core.exceptions import ProviderError
from ..db.connection import get_connection
from ..workspaces.store import assert_member


@dataclass(frozen=True)
class Scope:
    """One thing the scope picker can offer.

    ``id`` is ``None`` for the company, mirroring ``workspace_id IS NULL``
    everywhere else -- so a caller cannot accidentally pass "org" as a
    workspace id.
    """

    id: str | None
    name: str
    providers: list[str]


@dataclass(frozen=True)
class Freshness:
    """One connector's currency.

    ``needs_reauth`` is separate from an old ``last_sync_at`` because they need
    different copy: auto-sync skips a dead token entirely, so waiting will
    never make it current, and reporting only "last synced 6 days ago" invites
    someone to wait for a sync that can never happen.
    """

    provider: str
    last_sync_at: datetime | None
    needs_reauth: bool


def member_scopes(org_id: str, user_id: str) -> list[Scope]:
    """The company, plus every space this member belongs to.

    A space with nothing connected is still returned, carrying an empty
    ``providers`` list. Dropping it instead would make the space silently
    vanish from the picker -- the exact confusion the scheduler's space list
    caused before it started disclosing unschedulable spaces.
    """
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
    """Last sync per connector in one scope.

    Raises ``AuthError`` (from ``assert_member``) for a space the member is not
    in, rather than returning an empty list: empty reads as "that space has
    nothing connected", which is a different and misleading claim.
    """
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
