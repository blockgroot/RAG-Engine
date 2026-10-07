"""Google Drive: re-read one file that retrieval already returned (plan Phase 2).

Refresh-by-id only (plan D11): no search, no folder walk. The owner-only and
sharing rules were already applied by retrieval's visibility predicate, so this
reads exactly the file the asker was allowed to see. Google reports a rate
limit as a 403 too, so the ``reason`` decides, never the status alone (§5).
"""

from __future__ import annotations

import httpx

from ..sources.google_drive import (
    _DOC_MIME,
    _DOCX_MIME,
    _PDF_MIME,
    _extract_docx_text,
    _extract_pdf_text,
    _parse_dt,
)
from . import base
from .base import ProviderRead

_API = "https://www.googleapis.com/drive/v3/files"
#: A PDF/DOCX larger than this is not downloaded live; the indexed copy answers.
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024

_PERMISSION_REASONS = {"forbidden", "insufficientFilePermissions", "appNotAuthorizedToFile",
                       "notFound", "cannotDownloadFile"}
_RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded",
                 "sharingRateLimitExceeded"}


def read_file(token: str, external_id: str) -> ProviderRead:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        meta = httpx.get(
            f"{_API}/{external_id}",
            params={"fields": "name,mimeType,modifiedTime,trashed,size,"
                              "lastModifyingUser(displayName)",
                    "supportsAllDrives": "true"},
            headers=headers, timeout=base.TIMEOUT_SECONDS,
        )
        failure = _classify(meta)
        if failure is not None:
            return failure
        info = meta.json()
        if info.get("trashed"):
            return ProviderRead(base.NOT_ACCESSIBLE, reason="trashed")
        mime = info.get("mimeType")
        if mime == _DOC_MIME:
            body = httpx.get(f"{_API}/{external_id}/export", params={"mimeType": "text/markdown"},
                             headers=headers, timeout=base.TIMEOUT_SECONDS)
            failure = _classify(body)
            if failure is not None:
                return failure
            text = body.text
        elif mime in (_PDF_MIME, _DOCX_MIME):
            if int(info.get("size") or 0) > MAX_DOWNLOAD_BYTES:
                return ProviderRead(base.ERROR, reason="too_large")
            body = httpx.get(f"{_API}/{external_id}",
                             params={"alt": "media", "supportsAllDrives": "true"},
                             headers=headers, timeout=base.TIMEOUT_SECONDS)
            failure = _classify(body)
            if failure is not None:
                return failure
            text = (_extract_pdf_text if mime == _PDF_MIME else _extract_docx_text)(body.content)
        else:
            return ProviderRead(base.ERROR, reason=f"unsupported:{mime}")
    except httpx.TimeoutException:
        return ProviderRead(base.TIMEOUT)
    except httpx.HTTPError as exc:
        return ProviderRead(base.ERROR, reason=type(exc).__name__)

    parts = [info.get("name") or "Untitled file"]
    editor = (info.get("lastModifyingUser") or {}).get("displayName")
    when = _parse_dt(info.get("modifiedTime"))
    line = []
    if editor:
        line.append(f"last edited by {editor}")
    if when is not None:
        line.append(f"modified {when.strftime('%d %b %Y %H:%M UTC')}")
    if line:
        parts.append("Google Drive file, " + ", ".join(line) + ".")
    parts.append((text or "").strip())
    return ProviderRead(base.OK, text="\n\n".join(p for p in parts if p))


def _classify(response) -> ProviderRead | None:
    """``None`` when the response is usable, else the outcome it means."""
    status = response.status_code
    if status < 400:
        return None
    reasons = set()
    try:
        err = (response.json() or {}).get("error") or {}
        reasons = {str(e.get("reason")) for e in err.get("errors") or [] if e.get("reason")}
    except (ValueError, AttributeError):
        pass
    reason = ",".join(sorted(reasons)) or f"http_{status}"
    if status == 401:
        return ProviderRead(base.REAUTH, reason=reason)
    if status == 429 or reasons & _RATE_REASONS:
        return ProviderRead(base.RATE_LIMITED, reason=reason)
    if status == 404 or (status == 403 and reasons & _PERMISSION_REASONS):
        return ProviderRead(base.NOT_ACCESSIBLE, reason=reason)
    return ProviderRead(base.ERROR, reason=reason)
