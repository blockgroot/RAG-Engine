"""Other tools, through MCP servers an admin added (docs/plans/2026-10-21-mcp-actions.md).

A tool an admin set to "write" becomes an ``Action`` like every other: the
model fills in its form, code checks it, the person approves a card, and only
then is the tool called, once, with the person's own sign-in, from the stored
copy. MCP is only the last step's pipe; every guard before it is Handbook's.

What code cannot know about an unknown tool is treated as the worst case:

* Where it goes and who will see it are unknown, so private content always
  needs the tick (``Audience(members=None)``), as for an unknown audience.
* Every value that is not drafted text (an id, a project, an assignee) must be
  in the asker's words or in what a lookup returned in this request: the
  tool's own answer about what exists. Never from a document, never invented.
* Drafted text (title, body…) is cleaned like every draft: links and mentions
  only where the asker or a source wrote them.

A tool set to "lookup" is a read the model may run while drafting, with the
person's sign-in; its answer is fenced as untrusted data, like a passage.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..core.exceptions import ActionError, ActionRejected
from ..security.untrusted import scrub_untrusted_text
from . import mcp_client, mcp_store
from .base import Action, ActionContext, ActionResult, ActionTarget, Audience, Proposal, details
from .draft import asked_in, clean_body, clean_title

#: A field holds drafted words when its NAME says prose (schema field names,
#: not the asker's words): "title", "textContent", "pages[].content". The
#: first such top-level field becomes the card's title or body, editable like
#: every draft. A name ending in an id, an address or a type is never prose,
#: so "parent_page_id" and "mimeType" stay values to ground.
_PROSE = re.compile(r"title|^name$|summary|subject|heading|caption|body|description|text|content|"
                    r"notes?$|comment|message|markdown", re.IGNORECASE)
_TITLE = re.compile(r"title|^name$|summary|subject|heading", re.IGNORECASE)
_NOT_PROSE = re.compile(r"(id|ids|email|url|uri|type|format)$|user|assignee|owner", re.IGNORECASE)


def _prose(key: str) -> bool:
    return bool(_PROSE.search(key)) and not _NOT_PROSE.search(key)
_URL = re.compile(r"https://[^\s\"'<>)\]]+")
_FN = re.compile(r"[^A-Za-z0-9_]")


def action_name(server_id: str, tool: str) -> str:
    return f"mcp:{server_id}:{tool}"


def _function_name(server: mcp_store.McpServer, tool: str) -> str:
    """The model-facing name: ``^[A-Za-z0-9_]{1,64}$``, unique per server."""
    return f"mcp_{server.id.replace('-', '')[:6]}_{_FN.sub('_', tool)}"[:64]


def _schema(tool: dict) -> dict:
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
    return {"type": "object", "properties": dict(schema.get("properties") or {}),
            "required": [r for r in schema.get("required") or [] if isinstance(r, str)]}


def _describe(server: mcp_store.McpServer, tool: dict) -> str:
    """The server's own description is outside text: scrubbed and short. The
    admin read it before allowing the tool."""
    said = scrub_untrusted_text(tool.get("description") or "")[:300]
    return f"In {server.name}: {mcp_store.tool_title(tool, server.name)}. {said}".strip()


class McpToolAction(Action):
    """One "write" tool of one admin-added MCP server."""

    attribution = "as you"

    def __init__(self, server: mcp_store.McpServer, tool: dict) -> None:
        self.server = server
        self.mcp_tool = tool
        self.name = action_name(server.id, tool["name"])
        self.provider = server.provider
        self.label = f"{server.name}: {mcp_store.tool_title(tool, server.name)}"
        schema = _schema(tool)
        self.tool = {"type": "function", "function": {
            "name": _function_name(server, tool["name"]),
            "description": _describe(server, tool),
            "parameters": {**schema, "properties": {**schema["properties"], "request_words": {
                "type": "string", "description": "The exact words from the user's message that "
                "ask for this action, copied verbatim."}},
                "required": [*schema["required"], "request_words"]}}}

    def enabled_for(self, org_id: str, workspace_id: str | None) -> bool:
        from . import policy

        current = mcp_store.get_server(org_id, self.server.id)
        return bool(current and current.enabled and current.mode(self.mcp_tool["name"]) == "write"
                    and policy.act_on(org_id, workspace_id)
                    and policy.server_in_space(current, policy.space_sources(org_id, workspace_id))
                    and not policy.off_in_space(policy.space_off(org_id, workspace_id),
                                                self.server.id, self.mcp_tool["name"]))

    def token_for(self, org_id: str, user_id: str, surface: str = "web",
                  payload: dict[str, Any] | None = None) -> str:
        return "" if self.server.auth == "none" else super().token_for(org_id, user_id, surface)

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        schema = _schema(self.mcp_tool)
        props = schema["properties"]
        given = {k: v for k, v in args.items() if k in props and v not in (None, "")}
        missing = [k for k in schema["required"] if k not in given]
        if missing:
            raise ActionRejected(f"{self.server.name} needs {', '.join(missing)} for that. Say it in your request.")

        texty = [k for k, v in given.items() if isinstance(v, str) and _prose(k)]
        title_key = next((k for k in texty if _TITLE.search(k)), None)
        body_key = next((k for k in texty if k != title_key and not _TITLE.search(k)), None)
        uris = [s.uri for s in ctx.sources if s.uri]
        # The tool's own definition: a default or example it documents is its
        # own value (a mimeType), never something a passage put there.
        defined = json.dumps(self.mcp_tool, default=str)
        cleaned = {k: self._clean(k, v, props[k], ctx, uris, defined) for k, v in given.items()}
        if body_key and not cleaned.get(body_key):
            raise ActionRejected("The draft is empty.")
        preset = self.server.preset
        if preset and preset.target_check:
            preset.target_check(ctx.org_id, cleaned)  # e.g. GitHub: a company repository only

        payload: dict[str, Any] = {"args": cleaned, "fields": {"title": title_key, "body": body_key},
                                   "details": details(
                                       ("Can't be undone", f"Once approved, {self.server.name} can't reverse this "
                                        f"({mcp_store.tool_title(self.mcp_tool, self.server.name).lower()})."
                                        if mcp_store.destructive(self.mcp_tool) else None),
                                       *((k, _short(v)) for k, v in cleaned.items()
                                         if k not in (title_key, body_key)))}
        if title_key:
            payload["title"] = cleaned[title_key]
        if body_key:
            payload["body"] = cleaned[body_key]
        return Proposal(
            action=self.name,
            target=ActionTarget("mcp_tool", f"{self.server.id}:{self.mcp_tool['name']}", self.server.name),
            payload=payload,
            # Who sees what lands in an unknown tool cannot be checked: unknown,
            # so private content needs the tick and nothing is assumed small.
            audience=Audience(members=None),
        )

    def _clean(self, key: str, value: Any, prop: dict, ctx: ActionContext, uris: list[str], defined: str) -> Any:
        """One value, nested values included (Notion's ``pages`` is a list of
        objects). Prose is cleaned like every draft; anything else (a target,
        an id, a setting) must be in the asker's words, in what a lookup
        returned in this request, or in the tool's own definition."""
        prop = prop if isinstance(prop, dict) else {}
        if isinstance(value, dict):
            sub = prop.get("properties") or {}
            return {k: self._clean(k, v, sub.get(k) or {}, ctx, uris, defined) for k, v in value.items()}
        if isinstance(value, list):
            return [self._clean(key, v, prop.get("items") or {}, ctx, uris, defined) for v in value]
        if value is None or isinstance(value, bool) or value in (prop.get("enum") or ()):
            return value
        if isinstance(value, str) and _prose(key):
            return clean_title(value) if _TITLE.search(key) else clean_body(value, ctx.question, uris)
        text = str(value)
        if (asked_in(text, ctx.question) or (ctx.lookups and text in ctx.lookups)
                or (len(text) >= 3 and text in defined)):
            return value
        raise ActionRejected(f"{self.server.name}'s {key} must come from your request. Name it, then ask again.")

    def execute(self, key: str, target: dict[str, Any], payload: dict[str, Any], token: str) -> ActionResult:
        arguments = dict(payload.get("args") or {})
        for slot, field in (payload.get("fields") or {}).items():
            if field and slot in payload:
                arguments[field] = payload[slot]          # the person's edit, if any
        session = mcp_client.McpSession(self.server.url, token or None)
        session.open()
        result = session.call_tool(self.mcp_tool["name"], arguments, write=True)
        text = mcp_client.result_text(result, limit=2000)
        if result.get("isError"):
            raise ActionError(f"{self.server.name} refused it: {text[:300] or 'no reason given'}")
        structured = result.get("structuredContent") if isinstance(result.get("structuredContent"), dict) else {}
        link = structured.get("url") if str(structured.get("url") or "").startswith("https://") else None
        link = link or next(iter(_URL.findall(text)), None)
        return ActionResult(str(structured.get("id") or structured.get("identifier") or key), link)


def _short(value: Any) -> str:
    if isinstance(value, (dict, list)) and any(isinstance(v, (dict, list)) for v in
                                               (value.values() if isinstance(value, dict) else value)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
    return text if len(text) <= 120 else text[:117] + "…"


class McpLookup(Action):
    """One "lookup" tool: a read the model may run while drafting. Never a card."""

    lookup = True

    def __init__(self, server: mcp_store.McpServer, tool: dict) -> None:
        self.server = server
        self.mcp_tool = tool
        self.name = action_name(server.id, tool["name"])
        self.provider = server.provider
        self.label = f"{server.name}: {mcp_store.tool_title(tool, server.name)}"
        self.tool = {"type": "function", "function": {
            "name": _function_name(server, tool["name"]),
            "description": "Lookup (read only). " + _describe(server, tool),
            "parameters": _schema(tool)}}

    def run(self, args: dict[str, Any], token: str, *, timeout: float) -> str:
        """The tool's answer as text for the model, bounded. A failure is said, not raised."""
        props = _schema(self.mcp_tool)["properties"]
        try:
            session = mcp_client.McpSession(self.server.url, token or None, timeout=timeout)
            session.open()
            result = session.call_tool(self.mcp_tool["name"],
                                       {k: v for k, v in args.items() if k in props}, write=False)
        except Exception as exc:  # noqa: BLE001 - a failed read is reported to the model
            return f"[{self.label} failed: {str(exc)[:200]}]"
        return f"[{self.label}]\n{mcp_client.result_text(result)}"

    def prepare(self, args, ctx):  # pragma: no cover - a lookup is never drafted
        raise ActionRejected("That is a lookup, not an action.")

    def execute(self, key, target, payload, token):  # pragma: no cover
        raise ActionRejected("That is a lookup, not an action.")


def mcp_actions(org_id: str, user_id: str, workspace_id: str | None = None) -> list[Action]:
    """The person's MCP tools now: enabled servers they signed in to (or that
    need no sign-in), each tool in the mode an admin chose, minus what the
    space's owner switched off there. Never raises."""
    from ..auth.user_connections import list_user_connections
    from . import policy

    try:
        servers = [s for s in mcp_store.list_servers(org_id) if s.enabled]
        if not servers:
            return []
        signed_in = {c.provider for c in list_user_connections(org_id, user_id)}
        off = policy.space_off(org_id, workspace_id)
        sources = policy.space_sources(org_id, workspace_id)
        servers = [s for s in servers if policy.server_in_space(s, sources)]
    except Exception:  # noqa: BLE001 - MCP adds tools; it must never break Act
        return []
    out: list[Action] = []
    for server in servers:
        if server.auth != "none" and server.provider not in signed_in:
            continue
        for tool in server.tools:
            mode = server.mode(tool.get("name", ""))
            if policy.off_in_space(off, server.id, tool.get("name", "")):
                continue
            if mode == "write":
                out.append(McpToolAction(server, tool))
            elif mode == "lookup":
                out.append(McpLookup(server, tool))
    return out


def get_mcp_action(name: str, org_id: str | None) -> Action | None:
    """A stored row's MCP action, from the server as it is now."""
    parts = name.split(":", 2)
    if org_id is None or len(parts) != 3:
        return None
    try:
        server = mcp_store.get_server(org_id, parts[1])
    except Exception:  # noqa: BLE001 - a malformed id is an unknown action
        return None
    tool = server.tool(parts[2]) if server else None
    return McpToolAction(server, tool) if tool else None
