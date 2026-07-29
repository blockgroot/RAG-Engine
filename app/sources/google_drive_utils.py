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

import re

import httpx

from ..core.exceptions import ConfigurationError, SourceError

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
