"""MCP servers an admin added, and what each of their tools may do.

Every tool starts OFF. An admin sets each one to "lookup" (a read Handbook may
run while drafting, with the person's own sign-in, no card) or "write"
(drafted, shown on a card and run only on approval, like every action). The
choice is stored with the hash of the tool's schema and description: a tool
the server later changes is OFF again until an admin looks at it, so an
update on the server's side never widens what Handbook may do.

Nothing is blocked: the admin decides. A tool whose name or hints say it
deletes, merges, closes or rewrites is marked DESTRUCTIVE: "turn all on" skips
it (the admin turns it on by itself), and its card says it cannot be undone.
Like every write it still runs only on the person's approval. A server's own hints are only hints (MCP says so),
so "read-only" never makes a tool a lookup by itself; the admin must choose
it, and only a tool the server ALSO marks read-only can be a lookup.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from psycopg.types.json import Json

from ..db.connection import get_connection

MODES = ("off", "lookup", "write")

#: Names that delete, merge, close, move or rewrite things: destructive,
#: whatever the server says.
_DESTRUCTIVE = re.compile(
    r"(^|[_\-.\s])(delete|remove|destroy|drop|purge|archive|close|merge|revoke|transfer|"
    r"push|commit|reset|revert|force|ban|kick|deactivate|disable|cancel|wipe|truncate|"
    r"uninstall|rename|move|edit_file|write_file|create_branch|update_file)", re.IGNORECASE)


@dataclass(frozen=True)
class McpServer:
    id: str
    org_id: str
    name: str
    url: str
    auth: str
    oauth: dict | None
    tools: list[dict]
    tool_modes: dict
    enabled: bool

    @property
    def provider(self) -> str:
        """The ``user_connections.provider`` of a person's sign-in here."""
        return f"mcp:{self.id}"

    @property
    def preset(self):
        """The connector preset this server was added from, if any."""
        from .mcp_presets import preset_for

        return preset_for(self.url)

    def tool(self, name: str) -> dict | None:
        return next((t for t in self.tools if t.get("name") == name), None)

    def is_read(self, tool: dict) -> bool:
        preset = self.preset
        return read_only(tool) or bool(preset and preset.is_read(tool.get("name") or ""))

    def mode(self, name: str) -> str:
        """What a tool may do now: its admin-chosen mode, if the tool is still
        exactly what the admin chose it for; else off."""
        tool = self.tool(name)
        chosen = self.tool_modes.get(name) or {}
        if tool is None or chosen.get("hash") != tool_hash(tool):
            return "off"
        mode = chosen.get("mode")
        if mode == "lookup" and not self.is_read(tool):
            return "off"
        return mode if mode in MODES else "off"


def tool_hash(tool: dict) -> str:
    """What an admin approved: the name, description and input schema."""
    blob = json.dumps({k: tool.get(k) for k in ("name", "description", "inputSchema")},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def read_only(tool: dict) -> bool:
    return bool((tool.get("annotations") or {}).get("readOnlyHint"))


def destructive(tool: dict) -> bool:
    hints = tool.get("annotations") or {}
    return bool(_DESTRUCTIVE.search(tool.get("name") or "")) or (
        hints.get("destructiveHint") is True and not hints.get("readOnlyHint"))


_COLUMNS = "id::text, org_id::text, name, url, auth, oauth, tools, tool_modes, enabled"


def _row(r) -> McpServer:
    return McpServer(r[0], r[1], r[2], r[3], r[4], r[5], list(r[6] or []), dict(r[7] or {}), bool(r[8]))


def list_servers(org_id: str) -> list[McpServer]:
    with get_connection() as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM mcp_servers WHERE org_id = %s::uuid "
                            "ORDER BY created_at", (org_id,)).fetchall()
    return [_row(r) for r in rows]


def get_server(org_id: str, server_id: str) -> McpServer | None:
    with get_connection() as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM mcp_servers WHERE org_id = %s::uuid "
                           "AND id = %s::uuid", (org_id, server_id)).fetchone()
    return _row(row) if row else None


def create_server(org_id: str, *, name: str, url: str, auth: str, oauth: dict | None,
                  tools: list[dict], created_by: str | None) -> McpServer:
    with get_connection() as conn:
        row = conn.execute(
            f"INSERT INTO mcp_servers (org_id, name, url, auth, oauth, tools, created_by) "
            f"VALUES (%s::uuid, %s, %s, %s, %s, %s, %s::uuid) RETURNING {_COLUMNS}",
            (org_id, name, url, auth, Json(oauth) if oauth else None, Json(tools), created_by),
        ).fetchone()
    return _row(row)


def set_tools(org_id: str, server_id: str, tools: list[dict]) -> None:
    """A refreshed list. Modes stay keyed by hash, so a changed tool turns off."""
    with get_connection() as conn:
        conn.execute("UPDATE mcp_servers SET tools = %s, updated_at = now() "
                     "WHERE org_id = %s::uuid AND id = %s::uuid", (Json(tools), org_id, server_id))


def set_mode(org_id: str, server: McpServer, tool_name: str, mode: str) -> None:
    tool = server.tool(tool_name)
    modes = dict(server.tool_modes)
    if mode == "off" or tool is None:
        modes.pop(tool_name, None)
    else:
        modes[tool_name] = {"mode": mode, "hash": tool_hash(tool)}
    with get_connection() as conn:
        conn.execute("UPDATE mcp_servers SET tool_modes = %s, updated_at = now() "
                     "WHERE org_id = %s::uuid AND id = %s::uuid", (Json(modes), org_id, server.id))


def set_enabled(org_id: str, server_id: str, enabled: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE mcp_servers SET enabled = %s, updated_at = now() "
                     "WHERE org_id = %s::uuid AND id = %s::uuid", (enabled, org_id, server_id))


def delete_server(org_id: str, server_id: str) -> bool:
    """Gone, with everyone's sign-in to it (their tokens are forgotten too)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM user_connections WHERE org_id = %s::uuid AND provider = %s",
                     (org_id, f"mcp:{server_id}"))
        cur = conn.execute("DELETE FROM mcp_servers WHERE org_id = %s::uuid AND id = %s::uuid",
                           (org_id, server_id))
    return (cur.rowcount or 0) > 0


def tool_view(server: McpServer, tool: dict[str, Any]) -> dict:
    """What the admin page shows for one tool."""
    return {"name": tool.get("name"), "title": tool.get("title") or tool.get("name"),
            "description": (tool.get("description") or "")[:400],
            "read_only": server.is_read(tool), "destructive": destructive(tool),
            "mode": server.mode(tool["name"])}
