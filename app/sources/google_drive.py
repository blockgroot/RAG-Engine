"""Google Drive/Docs implementation of the ``SourceAdapter`` interface.

Phase 5 of the Google Integration Plan (see CLAUDE.md §2/§6 and
``GOOGLE_INTEGRATION_PLAN.md`` for the fuller reasoning). Structurally this
mirrors ``notion.py`` exactly — same class shape, same error-wrapping
(``SourceError``/``ConfigurationError`` with ``cause=``), same
helper-function decomposition — but the underlying calls are the Drive v3 /
Docs REST APIs over plain ``httpx``, not a vendor SDK (there is no
lightweight official Python client for this; the same "thin HTTP client, not
a framework" reasoning that put ``notion-client`` behind ``NotionAdapter`` and
``httpx`` behind ``GoogleOAuthProvider`` applies here too — see CLAUDE.md §1).

Three decisions worth calling out (all settled, not open questions):

1. **Extraction via ``files.export?mimeType=text/markdown``, not the Docs API's
   structural ``documents.get`` walk.** One Drive call returns ready-to-chunk
   Markdown (headings, lists, tables, links) that lands directly on what
   ``app/ingestion/preprocessing.py``/``chunking.py`` already assume, instead
   of us hand-rolling a Docs-JSON-tree-to-text renderer the way ``notion.py``
   has to for Notion's block tree.
2. **Folder-tree walk, not a flat single-level listing.** Drive's ``parents``
   field is direct-parent-only — there is no server-side "all descendants of
   this folder" query — so ``_walk_folder`` does its own breadth-first search
   across subfolders, accumulating every native Google Doc found anywhere
   under the root. This is the closer analogue to how Notion's ``child_page``
   blocks are treated as separate top-level documents (§ ``notion.py``
   ``_render_block``): a folder we're told to ingest should pull in its
   nested subfolders too, the same way an ingested Notion page's child pages
   are picked up as their own documents.
3. **Native Google Docs only this phase** (``mimeType =
   'application/vnd.google-apps.document'``). PDFs, Sheets, Slides, arbitrary
   files, and shortcuts are silently filtered out of ``list_documents()`` —
   they simply aren't in scope yet, the same way Notion's
   ``_exclude_index_parents`` silently drops folder/index pages that aren't
   real documents.

Auth: the adapter takes an already-resolved bare access token string. It has
no knowledge of OAuth, refresh, database connections, or ``org_id`` — exactly
like ``NotionAdapter`` doesn't know how its secret was obtained. Wiring this
adapter to a live, refreshed connection token is Phase 6's job
(``app/sources/factory.py``), not this module's.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from ..core.exceptions import ConfigurationError, SourceError
from .base import SourceAdapter, SourceDocument, SourceRef

_API_BASE = "https://www.googleapis.com/drive/v3"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_DOC_MIME = "application/vnd.google-apps.document"
_LIST_FIELDS = "nextPageToken,files(id,name,mimeType,modifiedTime,trashed,parents)"
_MAX_WALK_DEPTH = 20  # guard against pathological trees; Drive's own limit is ~100.


def _parse_dt(value: str | None) -> datetime | None:
    """Parse Drive's RFC3339 ``modifiedTime`` (e.g. ``2026-01-15T10:30:00.000Z``).

    Same ``Z``-suffix normalization trick as Notion's ``_parse_dt`` — Python's
    ``datetime.fromisoformat`` doesn't accept a bare ``Z`` before 3.11, so we
    swap it for an explicit UTC offset before parsing.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _doc_uri(file_id: str) -> str:
    return f"https://docs.google.com/document/d/{file_id}/edit"


class GoogleDriveAdapter(SourceAdapter):
    """Fetches native Google Docs living under one Drive folder (recursively)."""

    def __init__(self, token: str, folder_id: str, *, timeout: float = 15.0) -> None:
        """Build an adapter authenticated with an already-resolved access token.

        ``token`` and ``folder_id`` are both plain strings the caller already
        resolved (a live OAuth access token, and a folder id already parsed out
        of a pasted URL via ``extract_drive_folder_id``) — this class does not
        know about OAuth refresh, org scoping, or URL parsing, mirroring
        ``NotionAdapter.__init__`` taking a bare resolved secret.
        """
        if not token:
            raise ConfigurationError(
                "GoogleDriveAdapter requires a non-empty Google OAuth access token."
            )
        if not folder_id:
            raise ConfigurationError(
                "GoogleDriveAdapter requires a non-empty Drive folder id."
            )
        self._token = token
        self._folder_id = folder_id
        self._timeout = timeout

    # -- interface ---------------------------------------------------------

    def list_documents(self) -> list[SourceRef]:
        try:
            files = self._walk_folder(self._folder_id)
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"Google Drive files.list failed: {exc}", cause=exc) from exc

        return [
            SourceRef(
                external_id=file["id"],
                title=file.get("name", "Untitled"),
                last_modified=_parse_dt(file.get("modifiedTime")),
                source_uri=_doc_uri(file["id"]),
            )
            for file in files
        ]

    def fetch_document(self, external_id: str) -> SourceDocument:
        try:
            content = self._export_markdown(external_id)
            meta = self._get_file_metadata(external_id, fields="name,modifiedTime")
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(
                f"Google Drive fetch_document({external_id}) failed: {exc}", cause=exc
            ) from exc

        return SourceDocument(
            external_id=external_id,
            title=meta.get("name", "Untitled"),
            content=content,
            source_uri=_doc_uri(external_id),
            last_modified=_parse_dt(meta.get("modifiedTime")),
        )

    def get_last_modified(self, external_id: str) -> datetime | None:
        try:
            meta = self._get_file_metadata(external_id, fields="modifiedTime")
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(
                f"Google Drive get_last_modified({external_id}) failed: {exc}", cause=exc
            ) from exc
        return _parse_dt(meta.get("modifiedTime"))

    # -- HTTP helpers --------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _list_children(self, folder_id: str, page_token: str | None = None) -> dict:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": _LIST_FIELDS,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            response = httpx.get(
                f"{_API_BASE}/files",
                params=params,
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(f"Google Drive files.list failed: {exc}", cause=exc) from exc
        return response.json()

    def _walk_folder(self, root_folder_id: str) -> list[dict]:
        """Breadth-first walk of the folder tree, collecting native Google Docs.

        Drive's ``parents`` field is direct-parent-only, so there is no
        server-side recursive-descendant query (see module docstring). We walk
        ourselves: at each folder, list its direct children (paginating as
        needed), recurse into subfolders, and keep native Docs. A visited-set
        guards against a cycle (a subfolder pointing back at an ancestor), and
        ``_MAX_WALK_DEPTH`` bounds pathological/very-deep trees.
        """
        docs: list[dict] = []
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(root_folder_id, 0)]

        while queue:
            folder_id, depth = queue.pop(0)
            if folder_id in visited:
                continue
            visited.add(folder_id)
            if depth > _MAX_WALK_DEPTH:
                continue

            page_token: str | None = None
            while True:
                data = self._list_children(folder_id, page_token)
                for file in data.get("files", []):
                    mime = file.get("mimeType")
                    if mime == _FOLDER_MIME:
                        if file["id"] not in visited:
                            queue.append((file["id"], depth + 1))
                    elif mime == _DOC_MIME:
                        docs.append(file)
                    # else: PDFs/Sheets/Slides/shortcuts/other files — out of
                    # scope this phase, silently skipped.
                page_token = data.get("nextPageToken")
                if not page_token:
                    break

        return docs

    def _export_markdown(self, file_id: str) -> str:
        try:
            response = httpx.get(
                f"{_API_BASE}/files/{file_id}/export",
                params={"mimeType": "text/markdown"},
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Covers both transport errors and non-2xx status (including the
            # documented 10MB export-size-limit failure, typically a 403 with
            # reason ``exportSizeLimitExceeded``) — either way this is a
            # source-level failure, not a crash.
            raise SourceError(
                f"Google Drive files.export({file_id}) failed: {exc}", cause=exc
            ) from exc
        return response.text

    def _get_file_metadata(self, file_id: str, *, fields: str) -> dict:
        try:
            response = httpx.get(
                f"{_API_BASE}/files/{file_id}",
                params={
                    "fields": fields,
                    "supportsAllDrives": "true",
                },
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(
                f"Google Drive files.get({file_id}) failed: {exc}", cause=exc
            ) from exc
        return response.json()
