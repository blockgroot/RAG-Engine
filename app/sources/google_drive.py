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

logger = logging.getLogger(__name__)

_API_BASE = "https://www.googleapis.com/drive/v3"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_DOC_MIME = "application/vnd.google-apps.document"
_PDF_MIME = "application/pdf"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_SUPPORTED_MIMES = {_DOC_MIME, _PDF_MIME, _DOCX_MIME}
# `lastModifyingUser` costs nothing here: it rides along in the files.list
# request we already make, which is why "top editors" needs no extra call.
# `permissions` is the document-level ACL, and like `lastModifyingUser` it
# costs NOTHING extra: Drive returns it in the `files.list` request we already
# make on every sync. That is also what makes revocation work without a second
# sync loop -- an unshared file is re-stamped on the next listing even though
# its content never changed and is therefore never re-fetched.
_PERMISSION_FIELDS = "permissions(type,emailAddress,domain,deleted)"
_LIST_FIELDS = (
    "nextPageToken,files(id,name,mimeType,modifiedTime,trashed,parents,"
    f"lastModifyingUser(displayName),{_PERMISSION_FIELDS})"
)
#: Sentinel for "the connected account has not been looked up yet", which is
#: not the same as "looked up and Drive would not name it".
_UNSET = object()
_MAX_WALK_DEPTH = 20  # guard against pathological trees; Drive's own limit is ~100.


def _editor_name(file: dict) -> str | None:
    """Display name of whoever last modified the file, if Drive told us.

    Drive omits `lastModifyingUser` for some files (a service account edit, a
    deleted account, a shared drive with restricted metadata). None is the
    honest answer -- those rows simply do not appear in an editor chart, which
    is better than attributing them to someone.
    """
    user = file.get("lastModifyingUser") or {}
    name = (user.get("displayName") or "").strip()
    return name or None


def _file_access(
    file: dict, fallback_email: "Callable[[], str | None] | None" = None
) -> DocAccess | None:
    """Translate Drive's `permissions` array into a `DocAccess`.

    Drive omits `permissions` for real and it is not an error: reading a file's
    sharing list needs more than read access to the file, so a connecting
    account that is merely a viewer (or a Workspace with "viewers can see who
    else has access" switched off) gets the field left out.

    That case falls back to ``DocAccess.owner_only(fallback_email)`` -- indexed,
    but readable by the connected account alone. Without a ``fallback_email``
    there is nobody we can prove may read it, so this returns ``None`` and the
    pipeline SKIPS the document: indexing it readable would publish a file whose
    sharing we demonstrably could not read.

    Drive's four grant types map as:
      * ``anyone``  -> public within the scope ("anyone with the link")
      * ``user``    -> that person's email
      * ``domain``  -> ``domain:<host>``, matched against the asker's own host
      * ``group``   -> ``group:<address>``, which NO viewer can satisfy

    A `group` grant is stored as the GROUP, never expanded into its members:
    `sources.google_groups` resolves the asker's own memberships at query time
    instead, so the stored row keeps saying what Drive actually said and a new
    joiner is covered without touching a document. With GOOGLE_GROUPS_ENABLED
    off (the default) no viewer can satisfy a `group:` entry, so such a file
    stays withheld -- fail-closed on purpose, since the alternative is showing
    it to the whole scope, which is the bug this exists to fix.
    """
    permissions = file.get("permissions")
    if permissions is None:
        # Onyx's fallback: index it for the ONE account that could see the file
        # rather than dropping it. Retrying with `permissions.list` would be
        # pointless for us -- Drive omits the field precisely because this token
        # is not entitled to the sharing, and we have exactly one identity.
        # (Onyx's retry ladder pays off only because they impersonate a
        # retriever user, a fallback user and a Workspace admin in turn.)
        # A CALLABLE, resolved only here: a folder whose sharing is readable --
        # the normal case -- must not pay an `about.get` it never needs, and
        # `tests/test_google_drive_source.py` counts the calls a listing makes
        # for exactly this reason (the Slack `users.info` lesson, CLAUDE.md §5).
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

    document = docx.Document(BytesIO(data))
    return "\n\n".join(p.text for p in document.paragraphs)


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
        #: Lazily resolved connected-account address, for the unreadable-sharing
        #: fallback. `_UNSET` distinguishes "not looked up yet" from "looked up
        #: and Drive would not say", so a failed lookup is not retried per file.
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
                    "name,mimeType,modifiedTime,lastModifyingUser(displayName),"
                    f"{_PERMISSION_FIELDS}"
                ),
            )
            mime = meta.get("mimeType")
            if mime == _DOC_MIME:
                content = self._export_markdown(external_id)
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
        """The address this connection authenticates as, or ``None``.

        Resolved ONCE per adapter and reused, because it is the same answer for
        every file in the walk and the fallback path would otherwise spend a
        call per unreadable document. Never raises: failing to name the account
        only costs the fallback, which degrades to the skip that shipped first.
        """
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
        try:
            response = httpx.get(
                f"{_API_BASE}/files/{file_id}/export",
                params={"mimeType": "text/markdown"},
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(
                f"Google Drive files.export({file_id}) failed: {exc}", cause=exc
            ) from exc
        return response.text

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
