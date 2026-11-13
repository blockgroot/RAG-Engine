"""Google Drive adapter for native Docs plus PDF and DOCX files."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from io import BytesIO

import httpx

from ..config.settings import GoogleSettings
from ..core.exceptions import ConfigurationError, SourceError
from .base import DocAccess, SourceAdapter, SourceDocument, SourceRef
from .meta import build_meta, container, person

logger = logging.getLogger(__name__)

_API_BASE = "https://www.googleapis.com/drive/v3"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_DOC_MIME = "application/vnd.google-apps.document"
_PDF_MIME = "application/pdf"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_CSV_MIME = "text/csv"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Sheets and CSVs are TABLES (app/doctables): rows kept for charts, only a column
# description embedded (a figure read back from chunk text cannot be checked).
_TABLE_MIMES = {_SHEET_MIME, _CSV_MIME, _XLSX_MIME}
_SUPPORTED_MIMES = {_DOC_MIME, _PDF_MIME, _DOCX_MIME} | _TABLE_MIMES
# `lastModifyingUser` and `permissions` ride the files.list we already make, so
# an unshared file is re-stamped on the next listing without a re-fetch.
_PERMISSION_FIELDS = "permissions(type,emailAddress,domain,deleted)"
# Editor email and permissionId (stable key) link a person to a Handbook account;
# the graph never joins by display name.
_EDITOR_FIELDS = "lastModifyingUser(displayName,emailAddress,permissionId)"
_LIST_FIELDS = (
    "nextPageToken,files(id,name,mimeType,modifiedTime,trashed,parents,"
    f"lastModifyingUser(displayName),{_PERMISSION_FIELDS})"
)
#: Sentinel: "not looked up yet", unlike "looked up and Drive would not name it".
_UNSET = object()
_MAX_WALK_DEPTH = 20  # guard against pathological trees; Drive's own limit is ~100.


def _editor_name(file: dict) -> str | None:
    """Display name of whoever last modified the file, or None when Drive omits it (never guessed)."""
    user = file.get("lastModifyingUser") or {}
    name = (user.get("displayName") or "").strip()
    return name or None


def _file_meta(file: dict) -> dict | None:
    """People and containers from a `files.get` payload already in hand."""
    user = file.get("lastModifyingUser") or {}
    return build_meta(
        people=[
            person(
                "google",
                role="editor",
                external_id=user.get("permissionId"),
                email=user.get("emailAddress"),
                name=user.get("displayName"),
            )
        ],
        containers=[container("google", "folder", parent) for parent in file.get("parents") or []],
    )


def _file_access(
    file: dict, fallback_email: "Callable[[], str | None] | None" = None
) -> DocAccess | None:
    """Translate Drive's `permissions` array into a `DocAccess`.

    Drive omits `permissions` when the connecting account may not read the sharing.
    Then: ``owner_only(fallback_email)``, or ``None`` (skipped) with no fallback.

      * ``anyone``  -> public within the scope
      * ``user``    -> that email
      * ``domain``  -> ``domain:<host>``
      * ``group``   -> ``group:<address>``, expanded at read time from the asker's
        memberships (``google_groups``); with GOOGLE_GROUPS_ENABLED off it matches
        no one, so the file stays withheld (fail closed).
    """
    permissions = file.get("permissions")
    if permissions is None:
        # Index for the ONE account that could see the file: retrying with
        # permissions.list is pointless with the same token. A callable, so the normal
        # case pays no `about.get` (tests/test_google_drive_source.py counts calls).
        account = fallback_email() if fallback_email else None
        return DocAccess.owner_only(account) if account else None

    is_public = False
    viewers: list[str] = []
    for permission in permissions:
        if permission.get("deleted"):
            continue
        kind = permission.get("type")
        if kind == "anyone":
            is_public = True
        elif kind == "user":
            email = (permission.get("emailAddress") or "").strip().lower()
            if email:
                viewers.append(email)
        elif kind == "domain":
            domain = (permission.get("domain") or "").strip().lower()
            if domain:
                viewers.append(f"domain:{domain}")
        elif kind == "group":
            address = (permission.get("emailAddress") or "").strip().lower()
            if address:
                viewers.append(f"group:{address}")

    if is_public:
        return DocAccess.scope_public()
    return DocAccess.restricted(viewers)


def _extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader  # lazy: keep this out of the module import cost

    reader = PdfReader(BytesIO(data))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx_text(data: bytes) -> str:
    import docx  # lazy: keep this out of the module import cost

    from ..doctables.extract import render_docx_tables

    document = docx.Document(BytesIO(data))
    text = "\n\n".join(p.text for p in document.paragraphs)
    # `paragraphs` skips tables: render them as pipe tables (readable, and kept as rows).
    tables = render_docx_tables(document)
    return f"{text}\n\n{tables}" if tables else text


def _table_document(text: str, *, name: str, sheet: bool):
    """``(content, tables)`` for a Sheet or CSV: a column description to embed and
    the rows for charts. No usable table: indexed as raw text."""
    from ..doctables.extract import describe, parse_csv, profile

    raw = parse_csv(text, name=name)
    table = profile(raw) if raw is not None else None
    if table is None:
        return text, None
    if sheet:
        # CSV export is the FIRST tab only: the chart says so.
        raw = type(raw)(name=raw.name, header=raw.header, rows=raw.rows,
                        truncated=raw.truncated,
                        notes=("Only the first tab of this Google Sheet is read.",))
    return describe(table), [raw]


def _workbook_document(data: bytes, *, name: str):
    """``(content, tables)`` for a workbook: every tab via `doctables.extract.workbook_tables`."""
    from ..doctables.extract import describe, profile, workbook_tables

    raws = workbook_tables(name, data)
    profiled = [t for t in (profile(r) for r in raws) if t is not None]
    if not profiled:
        return "", None
    return "\n\n".join(describe(t) for t in profiled), raws


def _file_uri(file_id: str, mime: str) -> str:
    if mime == _DOC_MIME:
        return _doc_uri(file_id)
    return f"https://drive.google.com/file/d/{file_id}/view"


def _parse_dt(value: str | None) -> datetime | None:
    """Parse Drive's RFC3339 ``modifiedTime`` value."""
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

    def __init__(
        self,
        token: str,
        folder_id: str,
        *,
        timeout: float = 15.0,
        settings: GoogleSettings | None = None,
    ) -> None:
        """Build an adapter from a resolved access token and folder id."""
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
        limits = settings or GoogleSettings.from_env()
        self._max_walk_folders = max(1, limits.max_walk_folders)
        self._max_documents = max(1, limits.max_documents)
        #: Lazily resolved connected-account address for the fallback; `_UNSET` = not looked up yet.
        self._account: str | None | object = _UNSET

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
                source_uri=_file_uri(file["id"], file.get("mimeType", "")),
                last_editor=_editor_name(file),
                access=_file_access(file, self._account_email),
            )
            for file in files
        ]

    def fetch_document(self, external_id: str) -> SourceDocument:
        try:
            meta = self._get_file_metadata(
                external_id,
                fields=(
                    f"name,mimeType,modifiedTime,parents,{_EDITOR_FIELDS},"
                    f"{_PERMISSION_FIELDS}"
                ),
            )
            mime = meta.get("mimeType")
            tables = None
            if mime == _DOC_MIME:
                content = self._export_markdown(external_id)
            elif mime == _SHEET_MIME:
                content, tables = self._sheet_document(external_id, meta.get("name", "Untitled"))
            elif mime == _XLSX_MIME:
                content, tables = _workbook_document(
                    self._download_media(external_id), name=meta.get("name", "Untitled"))
            elif mime == _CSV_MIME:
                content, tables = _table_document(
                    self._download_media(external_id).decode("utf-8", errors="replace"),
                    name=meta.get("name", "Untitled"), sheet=False,
                )
            elif mime == _PDF_MIME:
                content = _extract_pdf_text(self._download_media(external_id))
            elif mime == _DOCX_MIME:
                content = _extract_docx_text(self._download_media(external_id))
            else:
                raise SourceError(f"Unsupported Google Drive file type: {mime!r}")
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
            source_uri=_file_uri(external_id, mime or ""),
            last_modified=_parse_dt(meta.get("modifiedTime")),
            last_editor=_editor_name(meta),
            access=_file_access(meta, self._account_email),
            meta=_file_meta(meta),
            tables=tables,
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

    def _account_email(self) -> str | None:
        """The address this connection authenticates as, or ``None``. Once per adapter;
        never raises (a failure just degrades the fallback to a skip)."""
        if self._account is not _UNSET:
            return self._account  # type: ignore[return-value]
        self._account = None
        try:
            response = httpx.get(
                f"{_API_BASE}/about",
                params={"fields": "user(emailAddress)"},
                headers=self._headers(),
                timeout=self._timeout,
            )
            if response.status_code < 400:
                email = ((response.json().get("user") or {}).get("emailAddress") or "").strip()
                self._account = email.lower() or None
            else:
                logger.warning(
                    "Google Drive about.get returned HTTP %s; files whose sharing "
                    "cannot be read will be skipped rather than indexed privately",
                    response.status_code,
                )
        except Exception:  # noqa: BLE001 - a missing fallback must not fail a sync
            logger.warning("Google Drive about.get failed", exc_info=True)
        return self._account  # type: ignore[return-value]

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
        """Breadth-first walk of one Drive folder tree, within configured bounds."""
        docs: list[dict] = []
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(root_folder_id, 0)]
        truncated: str | None = None

        while queue:
            if len(visited) >= self._max_walk_folders:
                truncated = (
                    f"folder limit reached ({self._max_walk_folders}); "
                    f"{len(queue)} subfolder(s) not visited"
                )
                break
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
                    elif mime in _SUPPORTED_MIMES:
                        docs.append(file)
                if len(docs) >= self._max_documents:
                    truncated = f"document limit reached ({self._max_documents})"
                    break
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
            if truncated:
                break

        if truncated:
            logger.warning(
                "Google Drive walk of folder %s was truncated: %s. Raise "
                "GOOGLE_MAX_WALK_FOLDERS / GOOGLE_MAX_DOCUMENTS, or point the "
                "connection at a narrower folder.",
                root_folder_id,
                truncated,
            )

        return docs[: self._max_documents]

    def _export_markdown(self, file_id: str) -> str:
        return self._export(file_id, "text/markdown")

    def _sheet_document(self, file_id: str, name: str):
        """Every tab of a Google Sheet, exported as .xlsx. Past Drive's export limit, the
        CSV export (first tab only) is read and the chart says so."""
        try:
            data = self._export_bytes(file_id, _XLSX_MIME)
        except SourceError:
            logger.warning("Drive: workbook export failed for %s; reading the first tab",
                           file_id, exc_info=True)
        else:
            content, tables = _workbook_document(data, name=name)
            if tables:
                return content, tables
        return _table_document(self._export(file_id, "text/csv"), name=name, sheet=True)

    def _export_bytes(self, file_id: str, mime: str) -> bytes:
        return self._export_response(file_id, mime).content

    def _export(self, file_id: str, mime: str) -> str:
        return self._export_response(file_id, mime).text

    def _export_response(self, file_id: str, mime: str) -> httpx.Response:
        try:
            response = httpx.get(
                f"{_API_BASE}/files/{file_id}/export",
                params={"mimeType": mime},
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(
                f"Google Drive files.export({file_id}) failed: {exc}", cause=exc
            ) from exc
        return response

    def _download_media(self, file_id: str) -> bytes:
        """Download raw bytes for a PDF or DOCX file."""
        try:
            response = httpx.get(
                f"{_API_BASE}/files/{file_id}",
                params={"alt": "media", "supportsAllDrives": "true"},
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(
                f"Google Drive files.get(alt=media, {file_id}) failed: {exc}", cause=exc
            ) from exc
        return response.content

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
