"""Parse and validate a Google Drive folder URL or id.

A Drive grant must be scoped to one folder (unlike Notion's shared pages), so
the admin's pasted URL or id is normalized here once for the API and the adapter.
No adapter import, so parsing stays light.
"""

from __future__ import annotations

import logging
import re
import time

import httpx

from ..core.exceptions import ConfigurationError, SourceError

logger = logging.getLogger(__name__)

_FOLDER_MIME = "application/vnd.google-apps.folder"
_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"

# A Drive folder URL, incl. the /u/<n>/ variant. Id length is undocumented.
_FOLDER_URL_RE = re.compile(
    r"drive\.google\.com/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)"
)

# A bare folder id; rejects only what is obviously not one (ids are undocumented).
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def extract_drive_folder_id(value: str) -> str:
    """Return the bare Drive folder id from a pasted folder URL (incl. ``/u/0/``) or raw id.

    Raises ``ConfigurationError`` for anything else, e.g. a Doc/Sheet link (a file,
    not a folder).
    """
    candidate = (value or "").strip()
    if not candidate:
        raise ConfigurationError(
            "Expected a Google Drive folder URL or folder id, got an empty value."
        )

    url_match = _FOLDER_URL_RE.search(candidate)
    if url_match:
        return url_match.group(1)

    if _BARE_ID_RE.fullmatch(candidate):
        return candidate

    raise ConfigurationError(
        "Expected a Google Drive folder URL (e.g. "
        "'https://drive.google.com/drive/folders/<id>') or a raw folder id, "
        f"got: {value!r}"
    )


def validate_drive_folder(token: str, folder_id: str, *, timeout: float = 15.0) -> dict:
    """Confirm ``folder_id`` is accessible and is a Drive folder, so a pasted Doc
    id fails now rather than as an empty ingest later.

    Returns ``{"folder_id", "folder_name"}`` for ``set_connection_config``.
    Raises ``ConfigurationError`` (not a folder, or 404: missing or invisible) or
    ``SourceError`` (unexpected failure).
    """
    try:
        response = httpx.get(
            f"{_DRIVE_FILES_URL}/{folder_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "fields": "id,name,mimeType",
                "supportsAllDrives": "true",
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise SourceError(
            f"Google Drive files.get failed for folder {folder_id!r}: {exc}",
            cause=exc,
        ) from exc

    if response.status_code == 404:
        raise ConfigurationError(
            f"Drive folder {folder_id!r} was not found or is not accessible "
            "with this Google connection. Share the folder with the connected "
            "account (or paste a different folder URL)."
        )
    if response.status_code >= 400:
        raise SourceError(
            f"Google Drive files.get returned HTTP {response.status_code} "
            f"for folder {folder_id!r}: {response.text}"
        )

    data = response.json()
    mime = data.get("mimeType")
    if mime != _FOLDER_MIME:
        raise ConfigurationError(
            f"Expected a Google Drive folder, but {folder_id!r} is "
            f"{mime!r}. Paste a folder URL (drive.google.com/.../folders/...), "
            "not a Doc or file link."
        )

    return {
        "folder_id": data.get("id") or folder_id,
        "folder_name": data.get("name") or folder_id,
    }


#: Files a preflight looks at: one `files.list` page, while someone waits on a button.
PREFLIGHT_SAMPLE = 25


def preflight_folder_sharing(
    token: str, folder_id: str, *, limit: int = PREFLIGHT_SAMPLE, timeout: float = 15.0
) -> dict:
    """Can we read WHO the files in this folder are shared with?

    Files whose sharing Drive omits are indexed for the connected account only, so
    this is asked on the picker, when the fix (make the account an owner or editor)
    is actionable. It names the files: the picker can already open them.

    Never raises: an unknown answer is "could not check".
    Returns ``{checked, unreadable, files, truncated, failed}``.
    """
    empty = {"checked": 0, "unreadable": 0, "files": [], "truncated": False, "failed": False}
    try:
        response = httpx.get(
            _DRIVE_FILES_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={
                "q": f"'{_escape_drive_query_value(folder_id)}' in parents and trashed = false",
                "fields": "nextPageToken,files(id,name,mimeType,permissions(type))",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "pageSize": max(1, min(limit, 100)),
            },
            timeout=timeout,
        )
        if response.status_code >= 400:
            logger.warning(
                "Drive sharing preflight for folder %s returned HTTP %s",
                folder_id, response.status_code,
            )
            return {**empty, "failed": True}
        payload = response.json()
    except Exception:  # noqa: BLE001 - a preflight must never fail the save
        logger.warning("Drive sharing preflight failed for folder %s", folder_id, exc_info=True)
        return {**empty, "failed": True}

    # Subfolders are not walked here; `truncated` says the answer is partial.
    files = [
        f for f in payload.get("files", []) if f.get("mimeType") != _FOLDER_MIME
    ]
    unreadable = [f for f in files if f.get("permissions") is None]
    has_subfolders = any(
        f.get("mimeType") == _FOLDER_MIME for f in payload.get("files", [])
    )
    return {
        "checked": len(files),
        "unreadable": len(unreadable),
        # A handful is enough to recognise the pattern.
        "files": [str(f.get("name") or "Untitled") for f in unreadable[:5]],
        "truncated": bool(payload.get("nextPageToken")) or has_subfolders,
        "failed": False,
    }


def _escape_drive_query_value(value: str) -> str:
    """Escape a value embedded in a Drive API ``q`` string literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def search_drive_folders(
    token: str, query: str = "", *, page_size: int = 20, timeout: float = 8.0
) -> list[dict]:
    """List Drive folders the connected account can see, optionally name-filtered,
    for the picker. Most recently modified first; ``[]`` for no match.

    Raises ``SourceError`` on an unexpected failure. ``timeout`` is short on
    purpose: a hung type-ahead looks broken; pasting a URL needs no search.
    """
    q_parts = [f"mimeType='{_FOLDER_MIME}'", "trashed=false"]
    clean_query = (query or "").strip()
    if clean_query:
        q_parts.append(f"name contains '{_escape_drive_query_value(clean_query)}'")

    started = time.monotonic()
    try:
        response = httpx.get(
            _DRIVE_FILES_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={
                "q": " and ".join(q_parts),
                "fields": "files(id,name)",
                "orderBy": "modifiedTime desc",
                "pageSize": str(page_size),
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise SourceError(f"Google Drive files.list failed: {exc}", cause=exc) from exc
    finally:
        # Logged: `elapsed` tells Drive's latency from our own in a slow picker.
        logger.info(
            "drive.folder_search elapsed=%.2fs filtered=%s",
            time.monotonic() - started,
            bool(clean_query),
        )

    if response.status_code >= 400:
        raise SourceError(
            f"Google Drive files.list returned HTTP {response.status_code}: {response.text}"
        )

    data = response.json()
    return [
        {"id": f["id"], "name": f.get("name") or f["id"]}
        for f in data.get("files", [])
        if f.get("id")
    ]
