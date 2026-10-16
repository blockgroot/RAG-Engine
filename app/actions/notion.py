"""Notion actions: comment on a page, add to a page, create a page under one,
add a row to a database.

They run on the person's token from the SEPARATE "Handbook Actions" Notion
connection (insert content, insert and read comments). Never the read
connection: a Notion token carries every capability of its connection, so a
write-capable read connection would make the admin's indexing token able to
edit every page it was shared.

Who Notion shows as the author: a comment carries ``display_name: user``, the
person's name. A page or block written through a public connection is very
likely recorded under the connection's bot (Notion documents no other
outcome), so those cards say "via Handbook Actions" instead of "as you".

Notion has no API for "may this person view, comment on or edit this page",
so nothing can be checked before the draft: a page Handbook indexed
company-wide must be one the asker may read there, the card says the write
needs that access, and Notion's own refusal is said per kind of write (can
only view, so no comment; can't edit, so no added text or new page).
Its readers cannot be listed, so the audience is always unknown.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..core.exceptions import (
    ActionError,
    ActionOutcomeUnknown,
    ActionRejected,
    OAuthReauthRequiredError,
)
from ..db.connection import get_connection
from ..security.visibility import viewer_clause
from .base import Action, ActionContext, ActionResult, ActionTarget, Audience, Proposal, details
from .draft import asked_in, clean_body, clean_title, require_named, without_title_heading

API = "https://api.notion.com/v1"
VERSION = "2026-03-11"
_TIMEOUT = 15.0
#: Notion's limits: 2,000 characters per rich-text object, 100 blocks per append.
TEXT_LIMIT = 2000
BLOCK_LIMIT = 100
_PAGE_ID = re.compile(r"([0-9a-f]{32})(?:[/?#]|$)|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
#: Notion cannot be asked what a person may do on a page, so these are said
#: when Notion itself refuses: on finding the page, and on each kind of write.
_CANT_OPEN = (
    "Handbook can't open that page. Share it with Handbook Actions (the page's ••• menu "
    "→ Connections) and check you can open it in Notion."
)
_CANT_COMMENT = (
    "Notion refused the comment: your account can only view this page. Ask the page's "
    "owner for comment access, then try again."
)
_CANT_EDIT = (
    "Notion refused: your account can't edit this page (you may only be able to view or "
    "comment). Ask the page's owner for edit access, then try again."
)

_REQUEST_WORDS = {
    "type": "string",
    "description": (
        "The exact words from the user's message that ask for this action, "
        "copied verbatim (for example 'add a comment')."
    ),
}
_PAGE_ARG = {
    "type": "string",
    "description": "The page's title or Notion link, exactly as the user wrote it.",
}


def _call(method: str, path: str, token: str, *, json: dict | None = None,
          refused: str = _CANT_OPEN) -> dict:
    write = method != "GET" and path != "/search"
    try:
        response = httpx.request(
            method, f"{API}{path}", json=json, timeout=_TIMEOUT,
            headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION},
        )
    except httpx.HTTPError as exc:
        if write:
            raise ActionOutcomeUnknown("Notion did not answer.", cause=exc) from exc
        raise ActionError("Could not reach Notion.", cause=exc) from exc
    if response.status_code == 401:
        raise OAuthReauthRequiredError("Reconnect your Notion account.")
    if write and response.status_code >= 500:
        raise ActionOutcomeUnknown(f"Notion answered {response.status_code}.")
    if response.status_code in (403, 404):
        raise ActionRejected(refused)
    if response.status_code == 429:
        raise ActionError("Notion is busy. Try again in a minute.")
    if response.status_code >= 400:
        raise ActionError(f"Notion answered {response.status_code}.")
    try:
        return response.json() or {}
    except ValueError as exc:
        if write:
            raise ActionOutcomeUnknown("Notion's answer was unreadable.", cause=exc) from exc
        raise ActionError("Notion's answer was unreadable.", cause=exc) from exc


def _title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if (prop or {}).get("type") == "title":
            return "".join(t.get("plain_text") or "" for t in prop.get("title") or []).strip()
    return ""


def _check_indexed_visibility(page_id: str, ctx: ActionContext) -> None:
    """A page Handbook indexed company-wide must be readable by the asker there."""
    bare = page_id.replace("-", "").lower()
    clause, params = viewer_clause(ctx.viewer, alias="d")
    with get_connection() as conn:
        rows = conn.execute(
            # NULL: not indexed (Notion decides). FALSE: indexed, but the asker
            # may not read it in Handbook. TRUE: indexed and readable.
            f"SELECT bool_or(TRUE{clause}) "
            "FROM documents d WHERE d.org_id = %s::uuid AND d.workspace_id IS NULL "
            "AND d.source_provider = 'notion' "
            "AND replace(lower(d.source_external_id), '-', '') = %s",
            (*params, ctx.org_id, bare),
        ).fetchone()
    indexed_and_visible = rows[0] if rows else None
    if indexed_and_visible is False:
        raise ActionRejected("You can't open that page in Handbook, so Handbook won't write to it for you.")


def resolve_page(name: str, ctx: ActionContext) -> dict:
    """The page the asker named: by its link, or by an exact, unique title
    among the pages they shared with Handbook Actions."""
    match = _PAGE_ID.search(name.lower())
    if match:
        page = _call("GET", f"/pages/{match.group(1) or match.group(2)}", ctx.token)
    else:
        found = _call("POST", "/search", ctx.token, json={
            "query": name, "page_size": 20,
            "filter": {"property": "object", "value": "page"},
        })
        wanted = name.casefold().strip()
        pages = [p for p in found.get("results") or [] if _title(p).casefold() == wanted]
        if not pages:
            raise ActionRejected(
                f"I can't find a page called {name!r} that you shared with Handbook Actions."
            )
        if len(pages) > 1:
            raise ActionRejected(f"Several pages are called {name!r}. Paste the page's link instead.")
        page = pages[0]
    if page.get("archived") or page.get("in_trash"):
        raise ActionRejected("That page is in Notion's trash.")
    _check_indexed_visibility(page["id"], ctx)
    return page


def _target(page: dict) -> ActionTarget:
    return ActionTarget("notion_page", page["id"], _title(page) or "Untitled", page.get("url"))


def rich_text(text: str) -> list[dict]:
    return [
        {"type": "text", "text": {"content": text[i:i + TEXT_LIMIT]}}
        for i in range(0, len(text), TEXT_LIMIT)
    ] or [{"type": "text", "text": {"content": ""}}]


#: Inline Markdown the model writes: a link, **bold**, `code`, *italic*/_italic_.
_INLINE = re.compile(
    r"\[(?P<label>[^\]\n]+)\]\((?P<url>https?://[^\s)]+)\)"
    r"|\*\*(?P<bold>[^*\n]+)\*\*"
    r"|`(?P<code>[^`\n]+)`"
    r"|(?<![\w*])\*(?P<star>[^*\n]+)\*(?![\w*])"
    r"|(?<![\w_])_(?P<under>[^_\n]+)_(?![\w_])"
)


def styled(text: str) -> list[dict]:
    """Inline Markdown as Notion rich text, so ``**Purpose:**`` arrives bold
    rather than with its asterisks. A link keeps only an http(s) address, and
    the body already lost every link the asker did not write (``clean_body``)."""
    out: list[dict] = []

    def add(chunk: str, **style) -> None:
        for i in range(0, len(chunk), TEXT_LIMIT):
            part = {"type": "text", "text": {"content": chunk[i:i + TEXT_LIMIT]}}
            if style.get("url"):
                part["text"]["link"] = {"url": style.pop("url")}
            if style:
                part["annotations"] = style
            out.append(part)

    at = 0
    for m in _INLINE.finditer(text):
        if m.start() > at:
            add(text[at:m.start()])
        if m.group("label"):
            add(m.group("label"), url=m.group("url"))
        elif m.group("bold"):
            add(m.group("bold"), bold=True)
        elif m.group("code"):
            add(m.group("code"), code=True)
        else:
            add(m.group("star") or m.group("under"), italic=True)
        at = m.end()
    if at < len(text):
        add(text[at:])
    return out or [{"type": "text", "text": {"content": ""}}]


def _block(kind: str, text: str, **extra) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": styled(text), **extra}}


_LINE_KINDS = (
    (re.compile(r"^#\s+(.*)"), "heading_1", {}),
    (re.compile(r"^##\s+(.*)"), "heading_2", {}),
    (re.compile(r"^#{3,6}\s+(.*)"), "heading_3", {}),
    (re.compile(r"^[-*+]\s+\[([ xX])\]\s+(.*)"), "to_do", {}),
    (re.compile(r"^[-*+]\s+(.*)"), "bulleted_list_item", {}),
    (re.compile(r"^\d{1,3}[.)]\s+(.*)"), "numbered_list_item", {}),
    (re.compile(r"^>\s?(.*)"), "quote", {}),
)


def blocks(body: str) -> list[dict]:
    """The body's Markdown as Notion blocks: headings, bullet, numbered and
    check lists, quotes, code fences and paragraphs. Never more than Notion
    takes in one call: the overflow joins the last block as plain text."""
    out: list[dict] = []
    para: list[str] = []
    lines = body.splitlines()

    def flush() -> None:
        if para:
            out.append(_block("paragraph", "\n".join(para)))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.lstrip().startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                code.append(lines[i])
                i += 1
            out.append({"object": "block", "type": "code",
                        "code": {"rich_text": rich_text("\n".join(code)), "language": "plain text"}})
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        stripped = line.strip()
        for pattern, kind, _ in _LINE_KINDS:
            m = pattern.match(stripped)
            if m:
                flush()
                if kind == "to_do":
                    out.append(_block(kind, m.group(2), checked=m.group(1) in "xX"))
                else:
                    out.append(_block(kind, m.group(1)))
                break
        else:
            para.append(line)
        i += 1
    flush()
    if len(out) > BLOCK_LIMIT:
        rest = "\n".join("".join(t["text"]["content"] for t in b[b["type"]]["rich_text"])
                         for b in out[BLOCK_LIMIT - 1:])
        out = out[: BLOCK_LIMIT - 1] + [_block("paragraph", rest)]
    return out


def _uris(ctx: ActionContext) -> list[str]:
    return [s.uri for s in ctx.sources if s.uri]


def _body(args: dict[str, Any], ctx: ActionContext) -> str:
    body = clean_body(args.get("body"), ctx.question, _uris(ctx))
    if not body:
        raise ActionRejected("The draft is empty.")
    return body


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {"request_words": _REQUEST_WORDS, **properties},
                       "required": ["request_words", *required]},
    }}


class NotionComment(Action):
    name = "notion.comment"
    provider = "notion"
    label = "Notion: comment on a page"
    tool = _tool("notion_comment", "Add a comment to a Notion page the user named.",
                 {"page": _PAGE_ARG, "body": {"type": "string", "description": "The comment."}},
                 ["page", "body"])

    def prepare(self, args, ctx) -> Proposal:
        page = resolve_page(require_named(args.get("page"), ctx.question, "Notion page"), ctx)
        return Proposal(self.name, _target(page), {"body": _body(args, ctx)}, Audience(members=None))

    def execute(self, key, target, payload, token) -> ActionResult:
        made = _call("POST", "/comments", token, json={
            "parent": {"page_id": target["external_id"]},
            "rich_text": styled(payload["body"]),
            "display_name": {"type": "user"},
        }, refused=_CANT_COMMENT)
        if not made.get("id"):
            raise ActionError("Notion did not add the comment.")
        return ActionResult(made["id"], target.get("url"))


class NotionAppend(Action):
    name = "notion.append"
    provider = "notion"
    label = "Notion: add to a page"
    attribution = "via Handbook Actions, for you"
    tool = _tool("notion_append", "Add text to the end of a Notion page the user named.",
                 {"page": _PAGE_ARG, "body": {"type": "string", "description": "The text to add."}},
                 ["page", "body"])

    def prepare(self, args, ctx) -> Proposal:
        page = resolve_page(require_named(args.get("page"), ctx.question, "Notion page"), ctx)
        return Proposal(self.name, _target(page), {"body": _body(args, ctx)}, Audience(members=None))

    def execute(self, key, target, payload, token) -> ActionResult:
        made = _call("PATCH", f"/blocks/{target['external_id']}/children", token,
                     json={"children": blocks(payload["body"])}, refused=_CANT_EDIT)
        if not made.get("results"):
            raise ActionError("Notion did not add the text.")
        return ActionResult(target["external_id"], target.get("url"))


class NotionCreatePage(Action):
    name = "notion.create_page"
    provider = "notion"
    label = "Notion: create a page"
    attribution = "via Handbook Actions, for you"
    tool = _tool(
        "notion_create_page", "Create a new Notion page inside a page the user named.",
        {"parent": {**_PAGE_ARG, "description": "The page to create it in, exactly as the user wrote it."},
         "title": {"type": "string", "description": "The new page's title."},
         "body": {"type": "string", "description": "The new page's text."}},
        ["parent", "title", "body"],
    )

    def prepare(self, args, ctx) -> Proposal:
        page = resolve_page(require_named(args.get("parent"), ctx.question, "Notion page"), ctx)
        title = clean_title(args.get("title"))
        return Proposal(self.name, _target(page), {
            "title": title,
            "body": without_title_heading(clean_body(args.get("body"), ctx.question, _uris(ctx)), title),
        }, Audience(members=None))

    def execute(self, key, target, payload, token) -> ActionResult:
        made = _call("POST", "/pages", token, json={
            "parent": {"page_id": target["external_id"]},
            "properties": {"title": {"title": rich_text(payload["title"])}},
            "children": blocks(payload["body"]),
        }, refused=_CANT_EDIT)
        if not made.get("id"):
            raise ActionError("Notion did not create the page.")
        return ActionResult(made["id"], made.get("url"))


#: Property types a row's field may set. People, relations, files and computed
#: types are left out: each would let a draft choose someone or something.
_ROW_TYPES = ("rich_text", "number", "select", "multi_select", "status", "date", "checkbox",
              "url", "email")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_data_source(name: str, ctx: ActionContext) -> dict:
    """The database the asker named, by its link or exact, unique title, among
    the ones shared with Handbook Actions, as its one data source (with its
    schema). A database with several data sources is refused, not guessed."""
    match = _PAGE_ID.search(name.lower())
    if match:
        database = _call("GET", f"/databases/{match.group(1) or match.group(2)}", ctx.token)
        sources = database.get("data_sources") or []
        if len(sources) != 1:
            raise ActionRejected("That database has several data sources. Name the one to add to.")
        source = _call("GET", f"/data_sources/{sources[0]['id']}", ctx.token)
    else:
        found = _call("POST", "/search", ctx.token, json={
            "query": name, "page_size": 20,
            "filter": {"property": "object", "value": "data_source"},
        })
        wanted = name.casefold().strip()
        hits = [d for d in found.get("results") or []
                if "".join(t.get("plain_text") or "" for t in d.get("title") or []).strip().casefold() == wanted]
        if not hits:
            raise ActionRejected(f"I can't find a database called {name!r} that you shared with Handbook Actions.")
        if len(hits) > 1:
            raise ActionRejected(f"Several databases are called {name!r}. Paste the database's link instead.")
        source = _call("GET", f"/data_sources/{hits[0]['id']}", ctx.token)
    if source.get("archived") or source.get("in_trash"):
        raise ActionRejected("That database is in Notion's trash.")
    return source


def _row_value(prop_name: str, prop: dict, value: str, question: str) -> dict:
    """One field as Notion's property value. Choices must already exist, and a
    link or email must be one the asker wrote."""
    kind = prop.get("type")
    if kind == "rich_text":
        return {"rich_text": rich_text(value)}
    if kind == "number":
        try:
            return {"number": float(value.replace(",", ""))}
        except ValueError:
            raise ActionRejected(f"{prop_name!r} takes a number, not {value!r}.") from None
    if kind in ("select", "status", "multi_select"):
        options = {o["name"].casefold(): o["name"] for o in (prop.get(kind) or {}).get("options") or []}
        wanted = [v.strip() for v in value.split(",")] if kind == "multi_select" else [value.strip()]
        missing = [v for v in wanted if v.casefold() not in options]
        if missing:
            raise ActionRejected(f"{prop_name!r} has no option {missing[0]!r}. Use one it already has.")
        names = [options[v.casefold()] for v in wanted]
        return {kind: [{"name": n} for n in names]} if kind == "multi_select" else {kind: {"name": names[0]}}
    if kind == "date":
        if not _ISO_DATE.match(value.strip()):
            raise ActionRejected(f"{prop_name!r} takes a date like 2026-10-30.")
        return {"date": {"start": value.strip()}}
    if kind == "checkbox":
        if value.strip().casefold() not in ("yes", "no", "true", "false"):
            raise ActionRejected(f"{prop_name!r} is a checkbox: say yes or no.")
        return {"checkbox": value.strip().casefold() in ("yes", "true")}
    if kind in ("url", "email"):
        if not asked_in(value, question):
            raise ActionRejected(f"Write the {kind} for {prop_name!r} in your request yourself.")
        return {kind: value.strip()}
    raise ActionRejected(f"Handbook can't fill {prop_name!r} (a {kind} field). Ask without it.")


class NotionAddRow(Action):
    name = "notion.add_row"
    provider = "notion"
    label = "Notion: add a row to a database"
    attribution = "via Handbook Actions, for you"
    tool = _tool(
        "notion_add_row", "Add a row (a new page) to a Notion database the user named.",
        {"database": {"type": "string", "description": "The database's title or link, exactly as the user wrote it."},
         "title": {"type": "string", "description": "The row's title (its name)."},
         "fields": {"type": "object", "additionalProperties": {"type": "string"}, "description":
                    "Other columns to fill, by their exact column name. Choices must be existing options; "
                    "dates YYYY-MM-DD; checkboxes yes/no; several choices comma-separated."},
         "body": {"type": "string", "description": "Optional text inside the row's page."}},
        ["database", "title"],
    )

    def prepare(self, args, ctx) -> Proposal:
        source = resolve_data_source(require_named(args.get("database"), ctx.question, "Notion database"), ctx)
        _check_indexed_visibility(source["id"], ctx)
        schema = source.get("properties") or {}
        title_prop = next((n for n, p in schema.items() if p.get("type") == "title"), None)
        if title_prop is None:
            raise ActionRejected("That database has no title column.")
        by_name = {n.casefold(): n for n in schema}
        properties, shown = {}, []
        fields = args.get("fields") if isinstance(args.get("fields"), dict) else {}
        for raw_name, raw_value in fields.items():
            name = by_name.get(str(raw_name).casefold())
            value = raw_value if isinstance(raw_value, str) else ""
            if name is None:
                raise ActionRejected(f"That database has no column called {raw_name!r}.")
            if name == title_prop or not value.strip():
                continue
            properties[name] = _row_value(name, schema[name], value, ctx.question)
            shown.append((name, value.strip()))
        title = clean_title(args.get("title"))
        body = clean_body(args.get("body"), ctx.question, _uris(ctx)) if args.get("body") else ""
        name = "".join(t.get("plain_text") or "" for t in source.get("title") or []).strip() or "Database"
        return Proposal(self.name, ActionTarget("notion_data_source", source["id"], name, source.get("url")), {
            "title": title, "body": body, "title_prop": title_prop,
            "properties": properties, "details": details(*shown),
        }, Audience(members=None))

    def execute(self, key, target, payload, token) -> ActionResult:
        made = _call("POST", "/pages", token, json={
            "parent": {"type": "data_source_id", "data_source_id": target["external_id"]},
            "properties": {**payload.get("properties", {}),
                           payload["title_prop"]: {"title": rich_text(payload["title"])}},
            **({"children": blocks(payload["body"])} if payload.get("body") else {}),
        }, refused=_CANT_EDIT)
        if not made.get("id"):
            raise ActionError("Notion did not add the row.")
        return ActionResult(made["id"], made.get("url"))
