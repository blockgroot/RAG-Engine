"""Notion actions: comment on a page, add to a page, create a page under one.

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
from .base import Action, ActionContext, ActionResult, ActionTarget, Audience, Proposal
from .draft import clean_body, clean_title, require_named

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


def paragraphs(body: str) -> list[dict]:
    """The body as paragraph blocks, one per blank-line-separated paragraph,
    never more than Notion takes in one call (the overflow joins the last)."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if len(parts) > BLOCK_LIMIT:
        parts = parts[: BLOCK_LIMIT - 1] + ["\n\n".join(parts[BLOCK_LIMIT - 1:])]
    return [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(p)}}
        for p in parts
    ]


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
            "rich_text": rich_text(payload["body"]),
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
                     json={"children": paragraphs(payload["body"])}, refused=_CANT_EDIT)
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
        return Proposal(self.name, _target(page), {
            "title": clean_title(args.get("title")),
            "body": clean_body(args.get("body"), ctx.question, _uris(ctx)),
        }, Audience(members=None))

    def execute(self, key, target, payload, token) -> ActionResult:
        made = _call("POST", "/pages", token, json={
            "parent": {"page_id": target["external_id"]},
            "properties": {"title": {"title": rich_text(payload["title"])}},
            "children": paragraphs(payload["body"]),
        }, refused=_CANT_EDIT)
        if not made.get("id"):
            raise ActionError("Notion did not create the page.")
        return ActionResult(made["id"], made.get("url"))
