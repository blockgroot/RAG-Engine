"""Which actions are switched on, and whether a person may send another write.

Act is off until an org admin allows it (``act_on``). In a space it is on
unless the owner switches it off; a space only narrows (an owner is an ordinary
member and cannot turn on what admins left off). Company-wide use follows the
company switch alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config.settings import ActionsSettings
from ..db.connection import get_connection
from . import store

#: The company-wide "Allow Act" switch (an ``action_policies`` row); with a
#: ``workspace_id`` it is that space owner's switch.
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


#: Surfaces besides the web, each its own ``surface:<kind>`` row.
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

    Only the org row can switch an action on; a space's ``act`` row is the owner's
    own switch (``act_on``).
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


def space_off(org_id: str, workspace_id: str | None) -> set[str]:
    """Tools a space's owner switched off there: ``mcp:<server>`` or
    ``mcp:<server>:<tool>``. Only "off" rows count, so it only narrows."""
    if workspace_id is None:
        return set()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT action FROM action_policies WHERE org_id = %s::uuid AND workspace_id = %s::uuid "
            "AND NOT enabled AND action LIKE 'mcp:%%'", (org_id, workspace_id)).fetchall()
    return {r[0] for r in rows}


def off_in_space(off: set[str], server_id: str, tool: str) -> bool:
    return f"mcp:{server_id}" in off or f"mcp:{server_id}:{tool}" in off


def space_sources(org_id: str, workspace_id: str | None) -> set[str] | None:
    """The connectors connected in this space (``None`` company-wide: no limit).
    A connector's tools are offered in a space only when it is connected there."""
    if workspace_id is None:
        return None
    from ..auth.credentials import list_connections

    return {c.provider for c in list_connections(org_id, workspace_id)}


def server_in_space(server, sources: set[str] | None) -> bool:
    """A connector's server belongs in a space that connected that connector; a
    server added by address (no connector) is the admin's, wherever Act is on."""
    preset = server.preset
    return sources is None or preset is None or preset.source is None or preset.source in sources


def within_rate(org_id: str, user_id: str, *, settings: ActionsSettings | None = None) -> bool:
    """Under the per-person hourly cap on executed writes."""
    settings = settings or ActionsSettings.from_env()
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    return store.executed_since(org_id, user_id, since) < settings.rate_per_hour
