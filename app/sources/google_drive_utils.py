"""Parse a Google Drive folder URL/ID into the bare folder id (Phase 4 of the
Google Integration Plan).

Google Drive has no equivalent of Notion's "whatever's shared with the
integration" model — a Drive OAuth grant that isn't scoped down to a specific
folder is both a tenant-isolation risk and broader than Google's OAuth scope
policy expects. The settled design (see CLAUDE.md / GOOGLE_INTEGRATION_PLAN.md)
is: the admin pastes a folder URL or raw id into the Sources page, and we parse
it into a folder id here, once, so both the admin API (Phase 6) and the Drive
adapter (Phase 5) work from the same normalized id. Kept as a standalone pure
function (no adapter import) so it has no dependency on the not-yet-built
``app/sources/google_drive.py`` adapter module.
"""

from __future__ import annotations

import re

from ..core.exceptions import ConfigurationError

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
