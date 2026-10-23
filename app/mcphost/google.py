"""Google Docs, Sheets and Drive through their regular REST APIs, with the
person's own token (scopes ``drive.file``, ``documents``, ``spreadsheets``:
none is restricted, so no yearly security assessment).

``drive.file`` reaches only files Handbook created or the person opened with
it, so a search finds those and a name resolves among them. Cells are written
RAW, never parsed: "=IMPORTDATA(…)" stays text and cannot fetch outside data.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from .base import HostedTool, ToolResult, VendorAuthError, VendorError, bounded

DRIVE = "https://www.googleapis.com/drive/v3/files"
DOCS = "https://docs.googleapis.com/v1/documents"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DOC_TYPE = "application/vnd.google-apps.document"
SHEET_TYPE = "application/vnd.google-apps.spreadsheet"
_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{25,}$")
_MAX_ROWS = 500


def _req(token: str, method: str, url: str, **kw) -> dict:
    try:
        r = httpx.request(method, url, headers={"Authorization": f"Bearer {token}"}, timeout=30, **kw)
    except httpx.HTTPError as exc:
        raise VendorError("Google did not answer.", cause=exc) from exc
    if r.status_code == 401:
        raise VendorAuthError("Sign in to Google again.")
    try:
        data = r.json() if r.content else {}
    except ValueError:
        data = {}
    if r.status_code >= 400:
        said = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else data.get("error")
        raise VendorError(f"Google refused it: {said or r.status_code}")
    return data


def _search(token: str, name: str | None, mime: str | None = None, exact: bool = False) -> list[dict]:
    terms = ["trashed = false"]
    if name:
        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        terms.append(f"name = '{safe}'" if exact else f"name contains '{safe}'")
    if mime:
        terms.append(f"mimeType = '{mime}'")
    return _req(token, "GET", DRIVE, params={
        "q": " and ".join(terms), "pageSize": 20, "orderBy": "modifiedTime desc",
        "fields": "files(id,name,mimeType,webViewLink,modifiedTime)"}).get("files") or []


def _file(token: str, ref: str, mime: str | None = None) -> dict:
    """One file by id or exact name, among those Handbook can open."""
    ref = ref.strip()
    if _FILE_ID.match(ref):
        return _req(token, "GET", f"{DRIVE}/{ref}", params={"fields": "id,name,mimeType,webViewLink"})
    found = _search(token, ref, mime, exact=True)
    if len(found) == 1:
        return found[0]
    kind = {DOC_TYPE: "Google Doc", SHEET_TYPE: "Google Sheet"}.get(mime or "", "file")
    if found:
        raise VendorError(f"Several files are called {ref}. Name it more exactly.")
    raise VendorError(f"There's no {kind} called {ref} that Handbook can open. Handbook reaches only "
                      "files it created or that you opened with it.")


def _rows(value) -> list[list[str]]:
    rows = [[str(c) for c in (r if isinstance(r, list) else [r])] for r in (value or [])]
    if not rows:
        raise VendorError("There are no rows to write.")
    if len(rows) > _MAX_ROWS:
        raise VendorError(f"That is more than {_MAX_ROWS} rows. Split it up.")
    return rows


# -- Tools ----------------------------------------------------------------------------


def search_files(token: str, args: dict) -> ToolResult:
    files = _search(token, str(args.get("query") or "") or None)
    rows = [f"{f['name']} ({f['mimeType'].rsplit('.', 1)[-1]}, id {f['id']}) {f.get('webViewLink', '')}" for f in files]
    return ToolResult(("\n".join(rows) or "Nothing found.") +
                      "\n[Handbook sees only files it created or that you opened with it]")


def _doc_text(doc: dict) -> str:
    out = []
    for block in (doc.get("body") or {}).get("content") or []:
        for el in (block.get("paragraph") or {}).get("elements") or []:
            out.append((el.get("textRun") or {}).get("content", ""))
    return "".join(out)


def read_file(token: str, args: dict) -> ToolResult:
    f = _file(token, str(args["file"]))
    if f["mimeType"] == DOC_TYPE:
        return ToolResult(bounded(f"{f['name']}:\n" + _doc_text(_req(token, "GET", f"{DOCS}/{f['id']}"))))
    if f["mimeType"] == SHEET_TYPE:
        rng = str(args.get("range") or "A1:Z200")
        values = _req(token, "GET", f"{SHEETS}/{f['id']}/values/{quote(rng, safe='!:')}").get("values") or []
        return ToolResult(bounded(f"{f['name']} {rng}:\n" + "\n".join(" | ".join(r) for r in values)))
    raise VendorError(f"{f['name']} isn't a Google Doc or Sheet, so it can't be read here.")


def create_doc(token: str, args: dict) -> ToolResult:
    doc = _req(token, "POST", DOCS, json={"title": str(args["title"])[:200]})
    text = str(args.get("content") or "")
    if text:
        _req(token, "POST", f"{DOCS}/{doc['documentId']}:batchUpdate",
             json={"requests": [{"insertText": {"location": {"index": 1}, "text": bounded(text, 100_000)}}]})
    url = f"https://docs.google.com/document/d/{doc['documentId']}/edit"
    return ToolResult(f"Created the Google Doc {doc.get('title')}.", doc["documentId"], url)


def create_sheet(token: str, args: dict) -> ToolResult:
    sheet = _req(token, "POST", SHEETS, json={"properties": {"title": str(args["title"])[:200]}})
    if args.get("rows"):
        _req(token, "POST", f"{SHEETS}/{sheet['spreadsheetId']}/values/A1:append",
             params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
             json={"values": _rows(args["rows"])})
    return ToolResult(f"Created the Google Sheet {args['title']}.", sheet["spreadsheetId"], sheet.get("spreadsheetUrl"))


def add_rows(token: str, args: dict) -> ToolResult:
    f = _file(token, str(args["spreadsheet"]), SHEET_TYPE)
    tab = str(args.get("sheet") or "").replace("'", "''")
    rng = f"'{tab}'!A1" if tab else "A1"
    rows = _rows(args["rows"])
    _req(token, "POST", f"{SHEETS}/{f['id']}/values/{quote(rng, safe='!:')}:append",
         params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}, json={"values": rows})
    return ToolResult(f"Added {len(rows)} row{'s' if len(rows) != 1 else ''} to {f['name']}.", f["id"],
                      f.get("webViewLink"))


def update_cells(token: str, args: dict) -> ToolResult:
    f = _file(token, str(args["spreadsheet"]), SHEET_TYPE)
    rng = str(args["range"])
    _req(token, "PUT", f"{SHEETS}/{f['id']}/values/{quote(rng, safe='!:')}",
         params={"valueInputOption": "RAW"}, json={"values": _rows(args["values"])})
    return ToolResult(f"Updated {rng} in {f['name']}.", f["id"], f.get("webViewLink"))


_ROWS = {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
         "description": 'Rows of cells, e.g. [["MCP test", "passed", "22 Oct"]].'}
_SHEET = {"type": "string", "description": "The Google Sheet's exact name."}

TOOLS = [
    HostedTool("search_files", "Search files", "Google files Handbook can open (it created them or you opened them with it).",
               {"properties": {"query": {"type": "string"}}}, True, search_files),
    HostedTool("read_file", "Read a Doc or Sheet", "The text of a Google Doc, or a range of a Google Sheet.",
               {"properties": {"file": {"type": "string", "description": "Exact name or id."},
                               "range": {"type": "string", "description": "A1 range for a Sheet."}},
                "required": ["file"]}, True, read_file),
    HostedTool("create_doc", "Create a Google Doc", "A new Google Doc with this title and text.",
               {"properties": {"title": {"type": "string"}, "content": {"type": "string"}},
                "required": ["title", "content"]}, False, create_doc),
    HostedTool("create_sheet", "Create a Google Sheet", "A new Google Sheet, optionally with rows.",
               {"properties": {"title": {"type": "string"}, "rows": _ROWS}, "required": ["title"]}, False, create_sheet),
    HostedTool("add_rows", "Add rows to a Sheet", "Append rows to a Google Sheet (values only, never formulas).",
               {"properties": {"spreadsheet": _SHEET, "sheet": {"type": "string", "description": "Tab name, if not the first."},
                               "rows": _ROWS}, "required": ["spreadsheet", "rows"]}, False, add_rows),
    HostedTool("update_cells", "Update cells", "Overwrite a range of cells in a Google Sheet (values only).",
               {"properties": {"spreadsheet": _SHEET, "range": {"type": "string", "description": 'A1 range, e.g. "A2:C2".'},
                               "values": _ROWS}, "required": ["spreadsheet", "range", "values"]}, False, update_cells),
]
