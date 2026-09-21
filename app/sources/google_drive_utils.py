"""Parse / validate a Google Drive folder URL/ID (Phase 4 of the Google
Integration Plan).

Google Drive has no equivalent of Notion's "whatever's shared with the
integration" model — a Drive OAuth grant that isn't scoped down to a specific
folder is both a tenant-isolation risk and broader than Google's OAuth scope
policy expects. The settled design (see CLAUDE.md / GOOGLE_INTEGRATION_PLAN.md)
is: the admin pastes a folder URL or raw id into the Sources page, and we parse
it into a folder id here, once, so both the admin API and the Drive adapter
work from the same normalized id. Kept as a standalone module (no adapter
import) so URL parsing stays usable without pulling in the full adapter.
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

# A Drive folder URL: https://drive.google.com/drive/folders/<id>[/...][?...]
# or the "switch account" variant with /u/<n>/ before "folders/". Google does
# not document a fixed length for the id, so we don't hardcode one — just
# capture the plausible id charset up to the next path segment or query string.
_FOLDER_URL_RE = re.compile(
    r"drive\.google\.com/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)"
)

# A bare folder id pasted directly, with no URL around it. Drive ids are
# typically 25-44 chars of [A-Za-z0-9_-], but that's not a documented
# guarantee, so this only rejects obviously-not-an-id input (empty, whitespace,
# something containing URL/path characters that isn't a matched URL above).
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def extract_drive_folder_id(value: str) -> str:
    """Return the bare Drive folder id from a pasted URL or raw id.

    Examples::

        extract_drive_folder_id(
            "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz?usp=sharing"
        ) == "1AbCdEfGhIjKlMnOpQrStUvWxYz"

        extract_drive_folder_id(
            "https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz"
        ) == "1AbCdEfGhIjKlMnOpQrStUvWxYz"

        extract_drive_folder_id("1AbCdEfGhIjKlMnOpQrStUvWxYz") == "1AbCdEfGhIjKlMnOpQrStUvWxYz"

    Raises:
        ConfigurationError: if ``value`` is empty or doesn't look like either
            a Drive folder URL or a plausible bare folder id (e.g. a Google
            Doc/Sheet link, which points at a *file* not a folder and is a
            different scope entirely).
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
    """Confirm ``folder_id`` is accessible and is actually a Drive folder.

    Calls ``files.get`` with the admin's live OAuth token so a pasted Doc/Sheet
    id or an inaccessible folder fails immediately with an actionable message,
    rather than silently producing an empty ingest later.

    Returns:
        ``{"folder_id": ..., "folder_name": ...}`` suitable for
        ``set_connection_config``.

    Raises:
        ConfigurationError: not a folder, or the id isn't accessible with this
            token (Drive returns 404 for both missing and invisible files).
        SourceError: unexpected Drive/HTTP failure.
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


#: How many files a preflight looks at. This is a check run while someone
#: waits on a button, not a sync -- one `files.list` page is enough to tell
#: "this folder's sharing is readable" from "none of it is", which is the only
#: question being asked.
PREFLIGHT_SAMPLE = 25


def preflight_folder_sharing(
    token: str, folder_id: str, *, limit: int = PREFLIGHT_SAMPLE, timeout: float = 15.0
) -> dict:
    """Can we read WHO the files in this folder are shared with?

    Document-level access filtering only indexes a file when Drive tells us its
    audience. Drive omits `permissions` when the connected account is not
    entitled to see a file's sharing -- typically when it is merely a reader or
    commenter on a file it does not own, or the Workspace has switched off
    "viewers and commenters can see who else has access". Those files are left
    out of the index rather than shown to the wrong people.

    Asked HERE, while the person is still standing on the folder picker, rather
    than reported after the fact: this is the one moment they are thinking
    about this folder, and the fix ("make the connected account an owner or
    editor") is one they can act on immediately. Waiting until after a sync
    means the first they learn of it is a question that will not answer.

    Unlike the notification bell, this NAMES the files: whoever is choosing the
    folder can already open it in Drive, so listing what is in it discloses
    nothing they do not have in front of them, and "which ones?" is the
    immediate next question.

    Never raises. A preflight that can fail the save it precedes would turn a
    diagnostic into an outage; an unknown answer is reported as "we could not
    check", which is honest and costs nothing.

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

    # Subfolders are not documents and carry no sharing question of their own
    # here -- the files inside them do, and those are a deeper walk than a
    # preflight should make. `truncated` is what says the answer is partial.
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
        # A handful is enough to recognise the pattern; a wall of filenames in
        # a warning box is not read.
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
    """List Drive folders the connected account can see, optionally name-filtered.

    Powers a folder-picker dropdown (search-as-you-type) so connecting a
    folder no longer requires the admin/workspace-owner to copy-paste its URL
    out of Drive — they just pick from folders Drive already says they can
    see, with the id resolved server-side. Uses the same live OAuth token as
    ``validate_drive_folder``.

    Returns:
        ``[{"id": ..., "name": ...}, ...]``, most-recently-modified first.
        Empty list for no matches — never raises for "nothing found".

    Raises:
        SourceError: unexpected Drive/HTTP failure (network, non-2xx).

    Note on ``timeout``: this powers a type-ahead dropdown, so it is bounded far
    below the usual API timeout deliberately. It used to be 15s, which meant a
    slow Drive response left the picker showing "Searching…" for fifteen seconds
    before surfacing anything — indistinguishable, to the person waiting, from a
    permanently hung UI (reported in production). Nobody waits 8s for a dropdown
    either, but at least the failure becomes an actionable error instead of an
    endless spinner. The user can always paste a folder URL instead, which needs
    no search at all.
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
        # Logged because this call is the one part of the folder picker we cannot
        # measure from outside: a slow picker could be our own DB round trips,
        # the token refresh, or Drive itself, and guessing between them is how
        # the ingest OOM took four attempts to diagnose. `elapsed` attributes it.
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
