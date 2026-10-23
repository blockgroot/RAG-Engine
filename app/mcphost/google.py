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
FOLDER_TYPE = "application/vnd.google-apps.folder"
_FIELDS = "id,name,mimeType,webViewLink"
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
        return _req(token, "GET", f"{DRIVE}/{ref}", params={"fields": _FIELDS})
    found = _search(token, ref, mime, exact=True)
    if len(found) == 1:
        return found[0]
    kind = {DOC_TYPE: "Google Doc", SHEET_TYPE: "Google Sheet", FOLDER_TYPE: "folder"}.get(mime or "", "file")
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


def _into(token: str, file_id: str, folder: str | None) -> str:
    """Put a file into the folder the asker named (by name or id), out of
    wherever it was; "" when none was named. A new file starts in My Drive."""
    if not str(folder or "").strip():
        return ""
    target = _file(token, str(folder), FOLDER_TYPE)
    parents = _req(token, "GET", f"{DRIVE}/{file_id}", params={"fields": "parents"}).get("parents") or []
    _req(token, "PATCH", f"{DRIVE}/{file_id}", json={},
         params={"addParents": target["id"], "removeParents": ",".join(parents), "fields": "id"})
    return f" in the folder {target['name']}"


def create_doc(token: str, args: dict) -> ToolResult:
    doc = _req(token, "POST", DOCS, json={"title": str(args["title"])[:200]})
    text = str(args.get("content") or "")
    if text:
        _req(token, "POST", f"{DOCS}/{doc['documentId']}:batchUpdate",
             json={"requests": [{"insertText": {"location": {"index": 1}, "text": bounded(text, 100_000)}}]})
    url = f"https://docs.google.com/document/d/{doc['documentId']}/edit"
    where = _into(token, doc["documentId"], args.get("folder"))
    return ToolResult(f"Created the Google Doc {doc.get('title')}{where}.", doc["documentId"], url)


def create_sheet(token: str, args: dict) -> ToolResult:
    sheet = _req(token, "POST", SHEETS, json={"properties": {"title": str(args["title"])[:200]}})
    if args.get("rows"):
        _req(token, "POST", f"{SHEETS}/{sheet['spreadsheetId']}/values/A1:append",
             params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
             json={"values": _rows(args["rows"])})
    where = _into(token, sheet["spreadsheetId"], args.get("folder"))
    return ToolResult(f"Created the Google Sheet {args['title']}{where}.", sheet["spreadsheetId"],
                      sheet.get("spreadsheetUrl"))


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


def append_to_doc(token: str, args: dict) -> ToolResult:
    f = _file(token, str(args["doc"]), DOC_TYPE)
    _req(token, "POST", f"{DOCS}/{f['id']}:batchUpdate", json={"requests": [
        {"insertText": {"endOfSegmentLocation": {}, "text": "\n" + bounded(str(args["text"]), 100_000)}}]})
    return ToolResult(f"Added the text to {f['name']}.", f["id"], f.get("webViewLink"))


def replace_in_doc(token: str, args: dict) -> ToolResult:
    f = _file(token, str(args["doc"]), DOC_TYPE)
    reply = _req(token, "POST", f"{DOCS}/{f['id']}:batchUpdate", json={"requests": [{"replaceAllText": {
        "containsText": {"text": str(args["find"]), "matchCase": True}, "replaceText": str(args["replace"])}}]})
    changed = ((reply.get("replies") or [{}])[0].get("replaceAllText") or {}).get("occurrencesChanged") or 0
    if not changed:
        raise VendorError(f"{f['name']} has no text \"{args['find']}\" to replace.")
    return ToolResult(f"Replaced {changed} place{'s' if changed != 1 else ''} in {f['name']}.", f["id"],
                      f.get("webViewLink"))


def add_sheet_tab(token: str, args: dict) -> ToolResult:
    f = _file(token, str(args["spreadsheet"]), SHEET_TYPE)
    _req(token, "POST", f"{SHEETS}/{f['id']}:batchUpdate",
         json={"requests": [{"addSheet": {"properties": {"title": str(args["tab"])[:100]}}}]})
    return ToolResult(f"Added the tab {args['tab']} to {f['name']}.", f["id"], f.get("webViewLink"))


def rename_file(token: str, args: dict) -> ToolResult:
    f = _file(token, str(args["file"]))
    done = _req(token, "PATCH", f"{DRIVE}/{f['id']}", params={"fields": _FIELDS},
                json={"name": str(args["new_name"])[:200]})
    return ToolResult(f"Renamed {f['name']} to {done.get('name')}.", f["id"], done.get("webViewLink"))


def copy_file(token: str, args: dict) -> ToolResult:
    f = _file(token, str(args["file"]))
    copy = _req(token, "POST", f"{DRIVE}/{f['id']}/copy", params={"fields": _FIELDS},
                json={"name": str(args.get("new_name") or f"Copy of {f['name']}")[:200]})
    where = _into(token, copy["id"], args.get("folder"))
    return ToolResult(f"Copied {f['name']} as {copy.get('name')}{where}.", copy.get("id"), copy.get("webViewLink"))


def create_folder(token: str, args: dict) -> ToolResult:
    folder = _req(token, "POST", DRIVE, params={"fields": _FIELDS},
                  json={"name": str(args["name"])[:200], "mimeType": FOLDER_TYPE})
    where = _into(token, folder["id"], args.get("folder"))
    return ToolResult(f"Created the folder {folder.get('name')}{where}.", folder.get("id"), folder.get("webViewLink"))


def move_file(token: str, args: dict) -> ToolResult:
    f = _file(token, str(args["file"]))
    where = _into(token, f["id"], str(args["folder"]))
    return ToolResult(f"Moved {f['name']}{where.replace(' in the', ' into the')}.", f["id"], f.get("webViewLink"))


def comment_on_file(token: str, args: dict) -> ToolResult:
    f = _file(token, str(args["file"]))
    _req(token, "POST", f"{DRIVE}/{f['id']}/comments", params={"fields": "id"},
         json={"content": bounded(str(args["comment"]), 4000)})
    return ToolResult(f"Commented on {f['name']}.", f["id"], f.get("webViewLink"))


#: Who a file may be shared as, in people's words, and Drive's name for each.
#: The asker's own word chooses it ("as an editor"); Drive's "writer" was never
#: said, so the role was left out and the file shared to view. Owner transfer
#: is never offered.
_ROLES = {"viewer": "reader", "commenter": "commenter", "editor": "writer"}


def share_file(token: str, args: dict) -> ToolResult:
    """Shared with one person, named by email. Handbook checks before the card
    that the person is in the company (``mcp_presets._google_target``)."""
    f = _file(token, str(args["file"]))
    role = str(args.get("role") or "viewer")
    if role not in _ROLES:
        raise VendorError(f"A file is shared as one of {', '.join(_ROLES)}.")
    _req(token, "POST", f"{DRIVE}/{f['id']}/permissions", params={"sendNotificationEmail": "true"},
         json={"type": "user", "role": _ROLES[role], "emailAddress": str(args["person"]).strip()})
    return ToolResult(f"Shared {f['name']} with {args['person']} as {role}.", f["id"], f.get("webViewLink"))


_ROWS = {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
         "description": 'Rows of cells, the column names first when the user names columns, '
                        'e.g. [["Test", "Result"], ["MCP test", "passed"]].'}
_SHEET = {"type": "string", "description": "The Google Sheet's exact name."}
_DOC = {"type": "string", "description": "The Google Doc's exact name."}
_FILE = {"type": "string", "description": "The file's exact name, or its id."}
_IN = {"type": "string", "description": "Only when the user names one: the folder to put it in, by its exact "
                                        "name. A folder another card of this request creates exists only once "
                                        "that card is approved: draft this one after it."}

TOOLS = [
    HostedTool("search_files", "Search files", "Google files Handbook can open (it created them or you opened them with it).",
               {"properties": {"query": {"type": "string"}}}, True, search_files),
    HostedTool("read_file", "Read a Doc or Sheet", "The text of a Google Doc, or a range of a Google Sheet.",
               {"properties": {"file": {"type": "string", "description": "Exact name or id."},
                               "range": {"type": "string", "description": "A1 range for a Sheet."}},
                "required": ["file"]}, True, read_file),
    HostedTool("create_doc", "Create a Google Doc", "A new Google Doc with this title and text, optionally in a folder.",
               {"properties": {"title": {"type": "string"}, "content": {"type": "string"}, "folder": _IN},
                "required": ["title", "content"]}, False, create_doc),
    HostedTool("create_sheet", "Create a Google Sheet", "A new Google Sheet, optionally with rows and in a folder.",
               {"properties": {"title": {"type": "string"}, "rows": _ROWS, "folder": _IN}, "required": ["title"]},
               False, create_sheet),
    HostedTool("add_rows", "Add rows to a Sheet", "Append rows to a Google Sheet (values only, never formulas).",
               {"properties": {"spreadsheet": _SHEET, "sheet": {"type": "string", "description": "Tab name, if not the first."},
                               "rows": _ROWS}, "required": ["spreadsheet", "rows"]}, False, add_rows),
    HostedTool("update_cells", "Update cells", "Overwrite a range of cells in a Google Sheet (values only).",
               {"properties": {"spreadsheet": _SHEET, "range": {"type": "string", "description": 'A1 range, e.g. "A2:C2".'},
                               "values": _ROWS}, "required": ["spreadsheet", "range", "values"]}, False, update_cells),
    HostedTool("append_to_doc", "Add text to a Doc", "Add text at the end of an existing Google Doc.",
               {"properties": {"doc": _DOC, "text": {"type": "string"}}, "required": ["doc", "text"]},
               False, append_to_doc),
    HostedTool("replace_in_doc", "Replace text in a Doc", "Replace every place an exact text appears in a Google Doc.",
               {"properties": {"doc": _DOC, "find": {"type": "string", "description": "The exact text now in the Doc."},
                               "replace": {"type": "string"}}, "required": ["doc", "find", "replace"]},
               False, replace_in_doc),
    HostedTool("add_sheet_tab", "Add a tab to a Sheet", "Add a new tab to a Google Sheet.",
               {"properties": {"spreadsheet": _SHEET, "tab": {"type": "string", "description": "The new tab's name."}},
                "required": ["spreadsheet", "tab"]}, False, add_sheet_tab),
    HostedTool("rename_file", "Rename a file", "Rename a Google Doc, Sheet or other file.",
               {"properties": {"file": _FILE, "new_name": {"type": "string"}}, "required": ["file", "new_name"]},
               False, rename_file),
    HostedTool("copy_file", "Copy a file", "Make a copy of a Google Doc, Sheet or other file.",
               {"properties": {"file": _FILE, "new_name": {"type": "string"}, "folder": _IN}, "required": ["file"]},
               False, copy_file),
    HostedTool("create_folder", "Create a folder", "A new folder in Google Drive, optionally inside another.",
               {"properties": {"name": {"type": "string"}, "folder": _IN}, "required": ["name"]}, False, create_folder),
    HostedTool("move_file", "Move a file", "Move a file into a folder.",
               {"properties": {"file": _FILE, "folder": {"type": "string", "description": "The folder's exact name."}},
                "required": ["file", "folder"]}, False, move_file),
    HostedTool("comment_on_file", "Comment on a file", "Add a comment to a Google Doc, Sheet or other file.",
               {"properties": {"file": _FILE, "comment": {"type": "string"}}, "required": ["file", "comment"]},
               False, comment_on_file),
    HostedTool("share_file", "Share a file", "Share a file with one person in the company, by email.",
               {"properties": {"file": _FILE, "person": {"type": "string", "description": "Their email address."},
                               "role": {"type": "string", "enum": list(_ROLES),
                                        "description": "viewer (the default), commenter or editor."}},
                "required": ["file", "person"]}, False, share_file),
]
