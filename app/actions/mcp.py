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
person's sign-in; its answer comes back to the model as a tool result, fenced
as untrusted data, and an id in it may fill the next form.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..core.exceptions import ActionError, ActionRejected
from ..security.untrusted import scrub_untrusted_text
from . import mcp_client, mcp_store
from .base import Action, ActionContext, ActionResult, ActionTarget, Audience, Proposal, details
from .draft import TYPOGRAPHY, asked_in, clean_body, clean_title

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


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def _shape(schema: Any) -> str:
    """One allowed form of a field, from its schema, for the model to copy
    (Notion's ``parent`` is "memory" or {"page_id": …}; the model sent a bare id)."""
    if not isinstance(schema, dict):
        return "a value"
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return " or ".join(json.dumps(v) for v in schema["enum"][:4])
    if schema.get("type") == "object" or schema.get("required"):
        return "{" + ", ".join(f'"{k}": …' for k in (schema.get("required") or list(schema.get("properties") or {})[:2])) + "}"
    return str(schema.get("type") or "a value")


def form_problem(schema: Any, value: Any, where: str = "the form") -> str | None:
    """What the server's own schema would refuse, said for the model to fix:
    types, allowed values, required and unknown keys, ``anyOf``/``oneOf``. A
    subset of JSON Schema, enough to catch a form filled in wrong before the
    person approves it (a write that fails after approval wastes their tick)."""
    if not isinstance(schema, dict) or value is None:
        return None
    for key in ("anyOf", "oneOf"):
        if isinstance(schema.get(key), list) and schema[key]:
            problems = [form_problem(sub, value, where) for sub in schema[key]]
            if all(problems):
                if len(problems) == 1:
                    return problems[0]
                shapes = ", ".join(_shape(sub) for sub in schema[key][:6])
                return f"{where} must be one of: {shapes}"
    kinds = schema.get("type")
    kinds = [k for k in (kinds if isinstance(kinds, list) else [kinds]) if k in _TYPES]
    if kinds and not any(isinstance(value, _TYPES[k]) and not (isinstance(value, bool) and k != "boolean")
                         for k in kinds):
        return f"{where} must be {' or '.join(kinds)}"
    if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
        return f"{where} must be one of {', '.join(map(str, schema['enum'][:10]))}"
    if isinstance(value, dict):
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        missing = [r for r in schema.get("required") or [] if isinstance(r, str) and r not in value]
        if missing:
            return f"{where} needs {', '.join(missing)}"
        if schema.get("additionalProperties") is False:
            unknown = [k for k in value if k not in props]
            if unknown:
                return f"{where} has no field {', '.join(unknown)} (its fields: {', '.join(props)})"
        for k, v in value.items():
            problem = form_problem(props.get(k), v, k if where == "the form" else f"{where}.{k}")
            if problem:
                return problem
    if isinstance(value, list):
        for i, v in enumerate(value):
            problem = form_problem(schema.get("items"), v, f"{where}[{i}]")
            if problem:
                return problem
    return None


class _Ungrounded(ActionRejected):
    """A value that is in neither the request, a lookup, nor the tool's definition."""


def _describe(server: mcp_store.McpServer, tool: dict) -> str:
    """The server's own description is outside text: scrubbed and short. The
    admin read it before allowing the tool."""
    said = scrub_untrusted_text(tool.get("description") or "")[:300]
    return f"In {server.name}: {mcp_store.tool_title(tool, server.name)}. {said}".strip()


def _code_label(text: str, prop: dict) -> str | None:
    """What the field's own description says a code means: Linear's priority
    says "3=Medium", so 3 is "Medium"; ``None`` when it defines no such code."""
    m = re.search(rf"(?<![\w.]){re.escape(text)}\s*[=:]\s*([A-Za-z][\w ]*?)\s*(?=[,;)]|$)",
                  str(prop.get("description") or ""))
    return m.group(1) if m else None


def _chosen_by_asker(key: str, value: Any, prop: dict, question: str) -> None:
    """An optional setting the tool offers as a list of choices is the
    asker's to make: GPT-OSS set Notion's ``creation_mode`` to "draft", which
    Notion refuses beside a parent, but only AFTER approval (no tool checks
    a form without acting on it). Left out, the tool's own default applies.
    Kept when the asker used the choice's words: "close the issue" is
    ``closed``, "reopen it" is ``open``."""
    if not isinstance(value, str) or value not in ((prop or {}).get("enum") or ()):
        return
    said = _WORDS.findall(question.translate(TYPOGRAPHY).lower())
    if all(any(w == x or (min(len(w), len(x)) >= 4 and (w in x or x in w)) for x in said)
           for w in _WORDS.findall(value.lower())):
        return
    raise _Ungrounded(f"{key} is a setting the request did not choose")


_WORDS = re.compile(r"[^\W_]+")
_EMPTY = (None, "", [], {})
_OWN_URL = re.compile(r'"(?:url|html_url|permalink|link)"\s*:\s*"(https://[^"\s]+)"')


def _guide(tool: dict) -> str:
    """The tool's whole manual, for a model whose form was refused: its full
    description (the card's 300 characters cut Notion's "set the title in
    properties" and every page form was wrong) and its parameters, scrubbed
    and bounded. Sent only after a refusal, so a call that gets the form right
    pays nothing for it."""
    said = scrub_untrusted_text(tool.get("description") or "")[:2000]
    params = json.dumps(_schema(tool)["properties"], ensure_ascii=False)
    params = params if len(params) <= 3000 else params[:3000] + "…"
    return f"How this tool is used: {said}\nIts parameters: {params}"


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
        props = {k: ({**v, "description": f"{v.get('description') or ''} Leave it out unless the user "
                      "said it: Handbook finds it from the company's connections.".strip()}
                     if k in self._filled and isinstance(v, dict) else v)
                 for k, v in schema["properties"].items()}
        self.tool = {"type": "function", "function": {
            "name": _function_name(server, tool["name"]),
            # Said here too: GPT-OSS reads the tool's description, not each field's.
            "description": _describe(server, tool) + "".join(
                f" Leave {k} out unless the user said it: Handbook fills it." for k in self._filled),
            "parameters": {**schema, "properties": {**props, "request_words": {
                "type": "string", "description": "The exact words from the user's message that "
                "ask for this action, copied verbatim."}},
                "required": [*(r for r in schema["required"] if r not in self._filled), "request_words"]}}}

    @property
    def _filled(self) -> tuple[str, ...]:
        preset = self.server.preset
        return preset.fills if preset and preset.target_check else ()

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

    def guide(self) -> str:
        return _guide(self.mcp_tool)

    def form_problem(self, args: dict[str, Any]) -> str | None:
        """``form_problem`` against this tool's own schema, on the fields the
        server will receive (not Handbook's ``request_words``)."""
        sent = {k: v for k, v in args.items()
                if k != "request_words" and v not in _EMPTY}
        schema = self.mcp_tool.get("inputSchema")
        if isinstance(schema, dict) and isinstance(schema.get("required"), list):
            schema = {**schema, "required": [r for r in schema["required"] if r not in self._filled]}
        return form_problem(schema, sent)

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        schema = _schema(self.mcp_tool)
        props = schema["properties"]
        # An empty value is "not given": GPT-OSS closed an issue with
        # ``labels: []``, which GitHub reads on an update as "remove them all".
        given = {k: v for k, v in args.items() if k in props and v not in _EMPTY}
        missing = [k for k in schema["required"] if k not in given and k not in self._filled]
        if missing:
            raise ActionRejected(f"{self.server.name} needs {', '.join(missing)} for that. Say it in your request.")

        texty = [k for k, v in given.items() if isinstance(v, str) and _prose(k)]
        title_key = next((k for k in texty if _TITLE.search(k)), None)
        body_key = next((k for k in texty if k != title_key and not _TITLE.search(k)), None)
        # A record's own address a lookup returned, like a document's own: "DM Raj
        # the link of the Linear issue" found BEN-161 by a lookup and lost its link.
        # Only the address field, never a link written inside a description.
        uris = [s.uri for s in ctx.sources if s.uri] + _OWN_URL.findall(ctx.lookups or "")
        # The tool's own definition: a default or example it documents is its
        # own value (a mimeType), never something a passage put there.
        defined = json.dumps(self.mcp_tool, default=str)
        # An optional value the asker never gave (a state, a label the model
        # added) is left out and said on the card; only a required one refuses.
        cleaned, left_out = {}, []
        for k, v in given.items():
            try:
                if k not in schema["required"]:
                    _chosen_by_asker(k, v, props[k], ctx.question)
                cleaned[k] = self._clean(k, v, props[k], ctx, uris, defined)
            except _Ungrounded:
                if k in schema["required"]:
                    raise
                left_out.append(f"{k} (not in your request)")
        # An optional field filled in wrong (Notion's ``parent`` in a shape it
        # does not take) is left out the same way; a required one refuses below.
        for k in [k for k in cleaned if k not in schema["required"]]:
            if form_problem(props[k], cleaned[k], k):
                del cleaned[k]
                left_out.append(f"{k} (not in a form {self.server.name} takes)")
        title_key, body_key = (k if k in cleaned else None for k in (title_key, body_key))
        problem = self.form_problem(cleaned)
        if problem:
            raise ActionRejected(f"{self.server.name}'s form wasn't filled in as it expects: {problem}.")
        if body_key and not cleaned.get(body_key):
            raise ActionRejected("The draft is empty.")
        preset = self.server.preset
        if preset and preset.target_check:
            preset.target_check(ctx.org_id, cleaned)  # e.g. GitHub: a company repository only
        unfilled = [k for k in schema["required"] if k not in cleaned]
        if unfilled:
            raise ActionRejected(f"{self.server.name} needs {', '.join(unfilled)} for that. Say it in your request.")

        payload: dict[str, Any] = {"args": cleaned, "fields": {"title": title_key, "body": body_key},
                                   "details": details(
                                       ("Can't be undone", f"Once approved, {self.server.name} can't reverse this "
                                        f"({mcp_store.tool_title(self.mcp_tool, self.server.name).lower()})."
                                        if mcp_store.destructive(self.mcp_tool) else None),
                                       ("Left out", ", ".join(left_out) or None),
                                       *((k, _named(v, ctx.lookups)) for k, v in cleaned.items()
                                         if k not in (title_key, body_key)))}
        target_link = _target_link(cleaned, ctx.lookups or "")
        if target_link:
            payload["target_link"] = target_link
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
        if asked_in(text, ctx.question):
            return value
        label = _code_label(text, prop)
        if label is not None:
            # A choice the field defines ("0=No priority") is the asker's to
            # make: "priority medium" is 3. A lookup never grounds one; any
            # lookup's text holds a "0" somewhere, and 0 rode onto the card.
            if asked_in(label, ctx.question):
                return value
        elif (ctx.lookups and text in ctx.lookups) or (len(text) >= 3 and text in defined):
            return value
        raise _Ungrounded(f"{self.server.name}'s {key} must come from your request. Name it, then ask again.")

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
        data = structured or mcp_client.result_json(result)
        # A URL the tool only echoed from what it was sent is content, not the
        # result's address: a Linear comment holding a GitHub link answered
        # with its body, and "Open in Linear" opened the GitHub issue.
        sent = json.dumps(arguments, ensure_ascii=False)
        echoed = [u for u in _URL.findall(text) if u.rstrip(".,;") not in sent]
        link = next((page for page in map(_page_link, [str(data.get("url") or ""), *echoed]) if page), None)
        if link is None and self.server.preset and self.server.preset.key == "github":
            link = _github_page(arguments)        # a merge answers with no link at all
        link = link or payload.get("target_link")
        # The tool's own id, as people name it (Linear's BEN-171 before its
        # uuid). Never Handbook's card id: told "id <card id>", the next
        # request commented on an issue that does not exist.
        found = data.get("identifier") or data.get("id")
        return ActionResult(str(found) if found else None, link)


def _target_link(args: dict[str, Any], lookups: str) -> str | None:
    """The page of the record a write acts on, for an answer with no link of
    its own: a Linear comment answers with ids only, and its card had nothing
    to open. Only a link a lookup returned beside that record's id (its
    ``url``-like field in the same object), or an earlier card's "id …, link …"."""
    ids = {v for k, v in args.items() if isinstance(v, str) and 3 <= len(v) <= 200 and not _prose(k)}
    if not ids or not lookups:
        return None
    for value in ids:
        m = re.search(rf"\bid {re.escape(value)}, link (https://\S+)", lookups)
        if m:
            return _page_link(m.group(1).rstrip(".,"))
    decoder, i = json.JSONDecoder(), lookups.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(lookups, i)
        except json.JSONDecodeError:
            i = lookups.find("{", i + 1)
            continue
        stack = [obj]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if ids & {v for v in node.values() if isinstance(v, str)}:
                    url = next((node[k] for k in ("url", "html_url", "permalink", "link")
                                if isinstance(node.get(k), str)), None)
                    page = _page_link(url) if url else None
                    if page:
                        return page
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        i = lookups.find("{", end)
    return None


_GITHUB_API = re.compile(r"^https://api\.github\.com/repos/([^/]+)/([^/]+)/(?:git/refs/heads/(.+)|(issues|pulls)/(\d+))$")


def _page_link(url: str) -> str | None:
    """A link a person can open: a GitHub API address becomes its page (a
    branch's tree, an issue or pull request), and no other API address is a
    link (one opened in a browser is a 404 for a private repo)."""
    url = url.rstrip(".,;")
    if not url.startswith("https://"):
        return None
    m = _GITHUB_API.match(url)
    if m:
        owner, repo, branch, kind, number = m.groups()
        return (f"https://github.com/{owner}/{repo}/tree/{branch}" if branch else
                f"https://github.com/{owner}/{repo}/{'pull' if kind == 'pulls' else 'issues'}/{number}")
    return None if url.split("/")[2].startswith("api.") else url


def _github_page(args: dict[str, Any]) -> str | None:
    """The pull request or issue a GitHub write acted on, from what it was sent."""
    owner, repo = args.get("owner"), args.get("repo")
    number = next((args[k] for k in ("pullNumber", "pull_number", "issue_number", "issueNumber") if args.get(k)), None)
    if not (owner and repo and number):
        return None
    kind = "issues" if "issue_number" in args or "issueNumber" in args else "pull"
    return f"https://github.com/{owner}/{repo}/{kind}/{number}"


_NAME = re.compile(r'"(name|title|displayName|key)"\s*:\s*"([^"\\]{1,80})"')


def _named(value: Any, lookups: str) -> str:
    """How the card shows a value: an id a lookup returned is shown by the
    name that lookup gave it ("Bench (BEN)", not "db73111d-…"); what is sent
    stays the id. Only from the same JSON object as the id, else the id."""
    if isinstance(value, str) and len(value) >= 8 and lookups and value in lookups:
        m = re.search(r'\{[^{}]*"' + re.escape(value) + r'"[^{}]*\}', lookups)
        said = dict(n.groups() for n in _NAME.finditer(m.group(0))) if m else {}
        name = said.get("name") or said.get("title") or said.get("displayName")
        key = said.get("key")
        if name or key:
            return f"{name} ({key})" if name and key and key != name else (name or key)
    return _short(value)


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
            # An empty value is "not given": GPT-OSS filled every optional
            # filter with "" or null, Linear refused them all, and it gave up.
            result = session.call_tool(self.mcp_tool["name"],
                                       {k: v for k, v in args.items() if k in props and v not in _EMPTY},
                                       write=False)
        except Exception as exc:  # noqa: BLE001 - a failed read is reported to the model
            return f"[{self.label} failed: {str(exc)[:200]}]"
        # Larger than a write's answer: a list must still show the item the
        # model is looking for (a team's issues ran past 4,000 characters).
        return f"[{self.label}]\n{mcp_client.result_text(result, limit=12000)}"

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


def get_mcp_action(name: str, org_id: str | None,
                   servers: dict[str, mcp_store.McpServer] | None = None) -> Action | None:
    """A stored row's MCP action, from the server as it is now. ``servers``
    (by id, read once) spares a list of cards one server read per card."""
    parts = name.split(":", 2)
    if org_id is None or len(parts) != 3:
        return None
    if servers is not None:
        server = servers.get(parts[1])
        tool = server.tool(parts[2]) if server else None
        return McpToolAction(server, tool) if tool else None
    try:
        server = mcp_store.get_server(org_id, parts[1])
    except Exception:  # noqa: BLE001 - a malformed id is an unknown action
        return None
    tool = server.tool(parts[2]) if server else None
    return McpToolAction(server, tool) if tool else None
