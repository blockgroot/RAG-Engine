"""Google Docs and Sheets actions: create a new Doc or Sheet in the asker's own
Drive, or add rows to a Sheet Handbook can already reach.

It runs on the person's own token with ``drive.file`` only: Google's
non-sensitive scope that reaches files the app creates (or the person picks),
nothing else in their Drive. Broad ``drive`` access is a restricted scope with
a yearly third-party security assessment; creating new Docs never needs it.

The new Doc is private to its creator, so its audience is the asker alone and
no private source is shown to anyone new. The connect flow also checks the
Google account IS the person's work account, so company content never lands
in a personal Drive.
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from typing import Any

import httpx

from ..core.exceptions import (
    ActionError,
    ActionOutcomeUnknown,
    ActionRejected,
    OAuthReauthRequiredError,
)
from .base import Action, ActionContext, ActionResult, ActionTarget, Audience, Proposal
from .draft import clean_body, clean_title, require_named, without_title_heading

#: The one Google scope Act asks a person for.
DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_TIMEOUT = 20.0


class DriveCreateDoc(Action):
    name = "drive.create_doc"
    provider = "google"
    label = "Google Docs: create a new doc"
    tool = {
        "type": "function",
        "function": {
            "name": "drive_create_doc",
            "description": "Create a new Google Doc in the user's own Google Drive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_words": {
                        "type": "string",
                        "description": (
                            "The exact words from the user's message that ask for this "
                            "action, copied verbatim (for example 'make a doc')."
                        ),
                    },
                    "title": {"type": "string", "description": "The Doc's title."},
                    "body": {"type": "string", "description": (
                        "The Doc's text in Markdown: '## ' headings, '- ' bullets, "
                        "**bold**. Never lines of '=' or '-' as rules or underlines.")},
                },
                "required": ["request_words", "title", "body"],
            },
        },
    }

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        body = clean_body(args.get("body"), ctx.question, [s.uri for s in ctx.sources if s.uri])
        if not body:
            raise ActionRejected("The draft is empty.")
        title = clean_title(args.get("title"))
        return Proposal(
            action=self.name,
            target=ActionTarget("google_drive", "my-drive", "Your Google Drive (only you can see it)"),
            payload={"title": title, "body": without_rules(without_title_heading(body, title))},
            audience=Audience(members=()),  # nobody but the asker
        )

    def execute(self, key, target, payload, token) -> ActionResult:
        # Markdown, which Google converts into Doc headings, lists and bold;
        # as text/plain the Doc showed the asterisks and hashes.
        return _upload(token, payload["title"], "application/vnd.google-apps.document",
                       "text/markdown", payload["body"], "Doc", payload.get("folder"))


_RULE_LINE = re.compile(r"^[ \t]*={3,}[ \t]*(?:\n|\Z)", re.M)
_BANNER = re.compile(r"^[ \t]*={2,}[ \t]*(\S.*?)[ \t]*=*[ \t]*$", re.M)


def without_rules(text: str) -> str:
    """'=====' rules dropped and '=== PART 1 ===' banners made '## ' headings.
    Google reads Markdown, where a '===' line under text makes it a heading,
    so plain-text banners came out as giant headings still showing the '='."""
    return _BANNER.sub(r"## \1", _RULE_LINE.sub("", text)).strip()


FOLDER = "application/vnd.google-apps.folder"


def _drive_get(token: str, path: str, fields: str, refused: str) -> dict:
    try:
        response = httpx.get(f"https://www.googleapis.com/drive/v3/files/{path}", timeout=_TIMEOUT,
                             params={"fields": fields, "supportsAllDrives": "true"},
                             headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise ActionError("Could not reach Google.", cause=exc) from exc
    if response.status_code == 401:
        raise OAuthReauthRequiredError("Reconnect your Google account.")
    if response.status_code in (403, 404):
        raise ActionRejected(refused)
    if response.status_code >= 400:
        raise ActionError(f"Google answered {response.status_code}.")
    return response.json() or {}


def shared_audience(token: str, file_id: str) -> Audience:
    """Who can open a file or folder. Shared with anyone or a whole domain is
    public; a group cannot be listed, so it is unknown, and so is sharing
    Google would not show."""
    try:
        perms = _drive_get(token, f"{file_id}/permissions", "permissions(type,emailAddress)",
                           "no sharing").get("permissions") or []
    except ActionRejected:
        return Audience(members=None)
    if any(p.get("type") in ("anyone", "domain") for p in perms):
        return Audience(public=True)
    if any(p.get("type") != "user" or not p.get("emailAddress") for p in perms):
        return Audience(members=None)
    return Audience(members=tuple(p["emailAddress"].lower() for p in perms))


def folder_audience(token: str, folder_id: str) -> tuple[str, Audience]:
    """A folder the person picked in Google's picker: its name and who can open
    what is created in it. Picking it is what lets drive.file reach it at all."""
    folder = _drive_get(token, folder_id, "id,name,mimeType,trashed,capabilities(canAddChildren)",
                        "Handbook can't open that folder. Pick it again in Google's window.")
    if folder.get("mimeType") != FOLDER or folder.get("trashed"):
        raise ActionRejected("That isn't a folder in your Drive.")
    if not (folder.get("capabilities") or {}).get("canAddChildren"):
        raise ActionRejected(f"You can't add files to {folder.get('name')!r}.")
    return folder.get("name") or "Folder", shared_audience(token, folder_id)


def _upload(token: str, title: str, google_type: str, content_type: str, text: str,
            what: str, folder: dict | None = None) -> ActionResult:
    """Create one Google file from text Google converts (Markdown to a Doc, CSV
    to a Sheet), in the folder the person picked if any. drive.file: the new
    file is the only one Handbook can reach."""
    boundary = f"handbook-{uuid.uuid4().hex}"
    metadata = {"name": title, "mimeType": google_type,
                **({"parents": [folder["id"]]} if folder else {})}
    content = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\nContent-Type: {content_type}; charset=UTF-8\r\n\r\n"
        f"{text}\r\n--{boundary}--\r\n"
    ).encode()
    try:
        response = httpx.post(
            UPLOAD_URL,
            params={"uploadType": "multipart", "fields": "id,name,webViewLink",
                    "supportsAllDrives": "true"},
            content=content,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": f"multipart/related; boundary={boundary}"},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ActionOutcomeUnknown("Google did not answer.", cause=exc) from exc
    if response.status_code == 401:
        raise OAuthReauthRequiredError("Reconnect your Google account.")
    if response.status_code >= 500:
        raise ActionOutcomeUnknown(f"Google answered {response.status_code}.")
    if response.status_code >= 400:
        raise ActionError(f"Google refused to create the {what} ({response.status_code}).")
    made = response.json() or {}
    if not made.get("id"):
        raise ActionError(f"Google did not create the {what}.")
    return ActionResult(made["id"], made.get("webViewLink"))


#: A Sheet's size limits for one draft. Past these, refuse rather than cut.
MAX_ROWS = 500
MAX_COLUMNS = 26
_FORMULA = re.compile(r"^[=+\-@\t\r]")


def _cell(value: object) -> str:
    return re.sub(r"\s+", " ", value if isinstance(value, str) else str(value or "")).strip()


def table_markdown(rows: list[list[str]]) -> str:
    """Rows as a Markdown table, the form the card shows and the person edits."""
    def line(cells): return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"
    return "\n".join([line(rows[0]), line(["---"] * len(rows[0])), *(line(r) for r in rows[1:])])


def table_rows(text: str) -> list[list[str]]:
    """A Markdown table back into rows; the separator line is dropped."""
    rows = []
    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            raise ActionRejected("A Sheet's draft must stay a table: one '| a | b |' line per row.")
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip("|"))]
        if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            rows.append(cells)
    _check_size(rows)
    return rows


def _check_size(rows: list[list[str]]) -> None:
    if not rows or not any(any(c for c in r) for r in rows):
        raise ActionRejected("The table is empty.")
    if len(rows) > MAX_ROWS or max(len(r) for r in rows) > MAX_COLUMNS:
        raise ActionRejected(f"A Sheet from Agent mode takes at most {MAX_ROWS} rows and {MAX_COLUMNS} columns.")


def sheet_csv(rows: list[list[str]]) -> str:
    """CSV for Google to convert. A cell that Sheets would read as a formula
    (=, +, -, @) is sent as text: a drafted formula could fetch outside data."""
    out = io.StringIO()
    csv.writer(out).writerows([["'" + c if _FORMULA.match(c) else c for c in r] for r in rows])
    return out.getvalue()


class DriveCreateSheet(Action):
    name = "drive.create_sheet"
    provider = "google"
    label = "Google Sheets: create a new sheet"
    tool = {
        "type": "function",
        "function": {
            "name": "drive_create_sheet",
            "description": "Create a new Google Sheet in the user's own Google Drive from a table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_words": {
                        "type": "string",
                        "description": ("The exact words from the user's message that ask for this "
                                        "action, copied verbatim (for example 'make a sheet')."),
                    },
                    "title": {"type": "string", "description": "The Sheet's title."},
                    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                             "description": "The table, header row first. Plain values, never formulas."},
                },
                "required": ["request_words", "title", "rows"],
            },
        },
    }

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        raw = args.get("rows") if isinstance(args.get("rows"), list) else []
        rows = [[_cell(c) for c in r] for r in raw if isinstance(r, list)]
        _check_size(rows)
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        # A table from documents, so links in it follow the body's rule.
        body = clean_body(table_markdown(rows), ctx.question, [s.uri for s in ctx.sources if s.uri])
        table_rows(body)
        return Proposal(
            action=self.name,
            target=ActionTarget("google_drive", "my-drive", "Your Google Drive (only you can see it)"),
            payload={"title": clean_title(args.get("title")), "body": body},
            audience=Audience(members=()),  # nobody but the asker
        )

    def edit(self, payload, fields, *, asked=None):
        new = super().edit(payload, fields, asked=asked)
        table_rows(new["body"])  # still a table, still within the limits
        return new

    def execute(self, key, target, payload, token) -> ActionResult:
        return _upload(token, payload["title"], "application/vnd.google-apps.spreadsheet",
                       "text/csv", sheet_csv(table_rows(payload["body"])), "Sheet", payload.get("folder"))


SHEET = "application/vnd.google-apps.spreadsheet"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"


def _sheets(method: str, path: str, token: str, *, params: dict | None = None,
            json_body: dict | None = None) -> dict:
    write = method != "GET"
    try:
        response = httpx.request(method, f"{SHEETS_API}/{path}", params=params, json=json_body,
                                 headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        if write:
            raise ActionOutcomeUnknown("Google did not answer.", cause=exc) from exc
        raise ActionError("Could not reach Google.", cause=exc) from exc
    if response.status_code == 401:
        raise OAuthReauthRequiredError("Reconnect your Google account.")
    if write and response.status_code >= 500:
        raise ActionOutcomeUnknown(f"Google answered {response.status_code}.")
    if response.status_code in (403, 404):
        raise ActionRejected("Google refused: you may not be allowed to edit that Sheet.")
    if response.status_code >= 400:
        raise ActionError(f"Google answered {response.status_code}.")
    return response.json() or {}


def _find_sheet(token: str, name: str) -> dict:
    """The one Sheet called ``name`` that Handbook can reach. With drive.file
    that is only Sheets Handbook created or the person picked, so a listing
    can never wander into the rest of their Drive."""
    quoted = name.replace("\\", "\\\\").replace("'", "\\'")
    try:
        response = httpx.get(
            "https://www.googleapis.com/drive/v3/files", timeout=_TIMEOUT,
            params={"q": f"name = '{quoted}' and mimeType = '{SHEET}' and trashed = false",
                    "fields": "files(id,name,webViewLink,capabilities(canEdit))", "pageSize": "10",
                    "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"},
            headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise ActionError("Could not reach Google.", cause=exc) from exc
    if response.status_code == 401:
        raise OAuthReauthRequiredError("Reconnect your Google account.")
    if response.status_code >= 400:
        raise ActionError(f"Google answered {response.status_code}.")
    found = (response.json() or {}).get("files") or []
    if not found:
        raise ActionRejected(
            f"Handbook can't find a Google Sheet called {name!r}. It can add rows only to Sheets "
            "it created for you in Agent mode.")
    if len(found) > 1:
        raise ActionRejected(f"More than one Sheet is called {name!r}. Rename one and ask again.")
    if not (found[0].get("capabilities") or {}).get("canEdit"):
        raise ActionRejected(f"You can't edit {name!r}.")
    return found[0]


class DriveAppendRows(Action):
    """Add rows under the table in an existing Sheet's first tab. The rows are
    sent RAW, so Sheets never reads a cell as a formula. The card shows the
    Sheet's own header with the new rows; only the new rows can be edited."""

    name = "drive.append_rows"
    provider = "google"
    label = "Google Sheets: add rows to a sheet"
    tool = {
        "type": "function",
        "function": {
            "name": "drive_append_rows",
            "description": "Add rows to the end of an existing Google Sheet the user named.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_words": {
                        "type": "string",
                        "description": ("The exact words from the user's message that ask for this "
                                        "action, copied verbatim (for example 'add to the sheet')."),
                    },
                    "sheet": {"type": "string", "description": "The Sheet's name exactly as the user wrote it."},
                    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                             "description": "Only the NEW rows, no header. Plain values, never formulas."},
                },
                "required": ["request_words", "sheet", "rows"],
            },
        },
    }

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        sheet = _find_sheet(ctx.token, require_named(args.get("sheet"), ctx.question, "Sheet"))
        meta = _sheets("GET", sheet["id"], ctx.token, params={"fields": "sheets.properties.title"})
        tab = (((meta.get("sheets") or [{}])[0]).get("properties") or {}).get("title") or "Sheet1"
        got = _sheets("GET", f"{sheet['id']}/values/{_a1(tab)}!1:1", ctx.token)
        header = [_cell(c) for c in ((got.get("values") or [[]])[0])]
        raw = args.get("rows") if isinstance(args.get("rows"), list) else []
        rows = [[_cell(c) for c in r] for r in raw if isinstance(r, list)]
        _check_size(rows)
        width = max(len(header), *(len(r) for r in rows))
        if header and width > len(header):
            raise ActionRejected(f"{sheet['name']!r} has {len(header)} columns; the rows have more.")
        header = header or [""] * width
        rows = [r + [""] * (width - len(r)) for r in rows]
        body = clean_body(table_markdown([header, *rows]), ctx.question, [s.uri for s in ctx.sources if s.uri])
        _new_rows(body, header)
        return Proposal(
            action=self.name,
            target=ActionTarget("google_sheet", sheet["id"], f"{sheet['name']} › {tab}", sheet.get("webViewLink")),
            payload={"body": body, "header": header, "tab": tab},
            audience=shared_audience(ctx.token, sheet["id"]),
        )

    editable_fields = ("body",)

    def edit(self, payload, fields, *, asked=None):
        new = super().edit(payload, fields, asked=asked)
        _new_rows(new["body"], payload["header"])
        return new

    def execute(self, key, target, payload, token) -> ActionResult:
        rows = _new_rows(payload["body"], payload["header"])
        _sheets("POST", f"{target['external_id']}/values/{_a1(payload['tab'])}!A1:append", token,
                params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                json_body={"values": rows})
        return ActionResult(target["external_id"], target.get("url"))


def _a1(tab: str) -> str:
    """A tab name quoted for A1 notation."""
    return "'" + tab.replace("'", "''") + "'"


def _new_rows(body: str, header: list[str]) -> list[list[str]]:
    """The edited table's rows under the Sheet's header, which must be unchanged."""
    rows = table_rows(body)
    if rows[0] != header and any(header):
        raise ActionRejected("Keep the Sheet's header row as it is; change only the new rows.")
    new = rows[1:]
    if not new:
        raise ActionRejected("There are no rows to add.")
    if any(len(r) != len(header) for r in new):
        raise ActionRejected(f"Each row needs {len(header)} cells, one per column.")
    return new
