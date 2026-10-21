"""Which actions are switched on, and whether a person may send another write.

Act itself is off until an org admin allows it for the company (``act_on``);
then each action is off unless an admin switched it on. With Act on, it is on
in every space too, and a space's owner may switch it OFF for their space. A
space only narrows what the company allows: a space owner is an ordinary member of the org, and letting
them turn on a write the org's admins left off would hand them a decision
that is not theirs. Company-wide use (the chat outside a space, Slack DMs)
follows the company switch alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config.settings import ActionsSettings
from ..db.connection import get_connection
from . import store

#: The company-wide "Allow Act" switch, stored as an ``action_policies`` row.
#: In a space, a row with this name and that ``workspace_id`` is the owner's
#: own switch for the space.
ACT_KEY = "act"


def act_on(org_id: str, workspace_id: str | None = None) -> bool:
    """May Act be used here? Company-wide: the admin's switch. In a space: also
    not switched off by the space's owner."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT workspace_id::text, enabled FROM action_policies "
            "WHERE org_id = %s::uuid AND action = %s "
            "AND (workspace_id IS NULL OR workspace_id = %s::uuid)",
            (org_id, ACT_KEY, workspace_id),
        ).fetchall()
    found = {r[0]: bool(r[1]) for r in rows}
    if not found.get(None):
        return False
    return workspace_id is None or found.get(workspace_id) is not False


#: Where Act may be asked from besides the web page, each switched on apart.
#: Stored as ``action_policies`` rows named ``surface:<kind>``.
SURFACES = {
    "slack_dm": "Allow Agent mode from Slack DMs",
    "slack_channel": "Allow Agent mode from Slack channels (replies stay in the thread)",
}


def surface_key(kind: str) -> str:
    return f"surface:{kind}"


def surface_enabled(org_id: str, kind: str) -> bool:
    """The web page needs no switch; a Slack surface needs its own."""
    return kind == "web" or surface_key(kind) in enabled_actions(org_id, None)


def enabled_actions(org_id: str, workspace_id: str | None) -> set[str]:
    """Actions on for the org and not switched off for this space."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT o.action
              FROM action_policies o
             WHERE o.org_id = %s::uuid AND o.workspace_id IS NULL AND o.enabled
               AND NOT EXISTS (
                   SELECT 1 FROM action_policies w
                    WHERE w.org_id = o.org_id AND w.workspace_id = %s::uuid
                      AND w.action = o.action AND NOT w.enabled
               )
            """,
            (org_id, workspace_id),
        ).fetchall()
    return {r[0] for r in rows}


def is_enabled(org_id: str, workspace_id: str | None, action: str) -> bool:
    return act_on(org_id, workspace_id) and action in enabled_actions(org_id, workspace_id)


def set_policy(
    org_id: str,
    action: str,
    enabled: bool,
    *,
    workspace_id: str | None = None,
    updated_by: str | None = None,
) -> None:
    """Switch an action on or off for the org (``workspace_id`` None) or a space.

    A space row that says ``enabled`` is stored but changes nothing for an
    action: only the org's row can switch one on (``enabled_actions``). The
    one exception is the space's own ``act`` row, the owner's opt-in when
    spaces decide (``act_on``).
    """
    conflict = (
        "ON CONFLICT (org_id, action) WHERE workspace_id IS NULL"
        if workspace_id is None
        else "ON CONFLICT (org_id, workspace_id, action) WHERE workspace_id IS NOT NULL"
    )
    with get_connection() as conn:
        conn.execute(
            f"""
            INSERT INTO action_policies (org_id, workspace_id, action, enabled, updated_by)
            VALUES (%s::uuid, %s::uuid, %s, %s, %s::uuid)
            {conflict} DO UPDATE SET enabled = EXCLUDED.enabled,
                updated_by = EXCLUDED.updated_by, updated_at = now()
            """,
            (org_id, workspace_id, action, enabled, updated_by),
        )


def within_rate(org_id: str, user_id: str, *, settings: ActionsSettings | None = None) -> bool:
    """Under the per-person hourly cap on executed writes."""
    settings = settings or ActionsSettings.from_env()
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    return store.executed_since(org_id, user_id, since) < settings.rate_per_hour
