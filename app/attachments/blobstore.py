"""The object store for attachments: original bytes plus cached plaintext.

Modelled on Onyx's `FileStore` (`backend/onyx/file_store/file_store.py` and
`file_store/utils.py`), narrowed to the four calls this codebase makes. Two
things are taken from that design on purpose:

1. **The extracted text is a SECOND object, not a re-parse.** Onyx writes
   `plaintext_{file_id}` beside the upload and reads it on every turn
   (`_get_or_extract_plaintext`), falling back to the original bytes only on a
   miss. Attachments ride on every turn of a chat, so without that cache a
   300-page PDF is re-parsed per question -- seconds of CPU and ~2x its size
   resident, on a 512MB box.
2. **The database keeps metadata, never content.** What is stored here is
   addressed by `public_id`; `conversation_attachments` holds the key, the
   filename and the counts.

Every asset is uploaded `type="authenticated"` with `resource_type="raw"`, and
every URL handed out is signed and short-lived. A default Cloudinary upload is
served from a permanent public URL, which for a tenant's contract means a link
that works for anyone it is forwarded to and keeps working after the chat is
deleted. Authenticated delivery is what makes this storage acceptable for this
content; it is not a hardening pass to do later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config.settings import CloudinarySettings
from ..core.exceptions import ProviderError

logger = logging.getLogger(__name__)


class AttachmentStorageError(ProviderError):
    """The object store could not be reached, or refused the operation."""


@dataclass(frozen=True)
class StoredObject:
    """What the database needs to find this asset again."""

    #: Cloudinary's `public_id`. The only handle we persist -- a URL is not
    #: stored, because a signed one expires and an unsigned one should not
    #: exist.
    key: str
    bytes_written: int


def plaintext_key(key: str) -> str:
    """The companion object holding this upload's extracted text.

    Onyx's `plaintext_file_name_for_id` naming, kept so the relationship is
    legible in a bucket listing: an original and its text sit next to each
    other under one prefix.
    """
    return f"{key}__plaintext"


def _client():
    """The configured Cloudinary SDK, or a raise naming what is missing.

    Imported INSIDE the function: CLAUDE.md §5 -- a module-level import of a
    third-party SDK costs boot time on every process, including the ones that
    never touch an attachment.
    """
    settings = CloudinarySettings.from_env()
    if not settings.configured:
        raise AttachmentStorageError(
            "Cloudinary is not configured: set CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET."
        )
    try:
        import cloudinary
        import cloudinary.api  # noqa: F401  (registers the submodule)
        import cloudinary.uploader  # noqa: F401
        import cloudinary.utils  # noqa: F401
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise AttachmentStorageError(
            "The cloudinary package is not installed", cause=exc
        ) from exc

    cloudinary.config(
        cloud_name=settings.cloud_name,
        api_key=settings.api_key,
        api_secret=settings.api_secret,
        secure=True,
    )
    return cloudinary, settings


def save_bytes(key: str, data: bytes, *, content_type: str | None = None) -> StoredObject:
    """Upload one asset under ``key``, overwriting any asset already there.

    ``resource_type="raw"`` because these are documents, not media: Cloudinary
    would otherwise try to decode a PDF as an image and transform it. The
    upload is `authenticated`, so nothing here is reachable without a signature
    even if the public_id is guessed.
    """
    cloudinary, settings = _client()
    try:
        result = cloudinary.uploader.upload(
            data,
            public_id=key,
            folder=settings.folder,
            resource_type="raw",
            type="authenticated",
            access_mode="authenticated",
            # An attachment is replaced in place when a file is re-uploaded to
            # the same row; without this Cloudinary refuses the second write.
            overwrite=True,
            # The filename is already on the database row and is member-
            # supplied text; letting it shape the public_id would put it in a
            # URL and make the key unpredictable.
            use_filename=False,
            unique_filename=False,
            resource_metadata=False,
            context={"content_type": content_type} if content_type else None,
        )
    except Exception as exc:  # noqa: BLE001 - SDK raises several unrelated types
        raise AttachmentStorageError(
            f"Could not store attachment {key}", cause=exc
        ) from exc
    return StoredObject(key=result["public_id"], bytes_written=len(data))


def save_text(key: str, text: str) -> StoredObject:
    """Store extracted text as its own asset. UTF-8, no transformation."""
    return save_bytes(key, text.encode("utf-8"), content_type="text/plain")


def read_bytes(key: str) -> bytes:
    """Fetch one asset. Raises rather than returning empty.

    An empty return would be indistinguishable from a genuinely empty file and
    would reach the prompt as "this document says nothing", which is a claim
    about the document rather than about the fetch.
    """
    cloudinary, settings = _client()
    import httpx

    url = signed_url(key)
    try:
        response = httpx.get(url, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise AttachmentStorageError(
            f"Could not read attachment {key}", cause=exc
        ) from exc
    return response.content


def read_text(key: str) -> str:
    """Fetch one asset as text. Never raises on a bad byte -- an attachment's
    encoding is a property of somebody else's file (`extract._decode`)."""
    return read_bytes(key).decode("utf-8", errors="replace")


def signed_url(key: str, *, ttl_seconds: int | None = None) -> str:
    """A short-lived signed URL for one asset.

    `sign_url=True` plus an expiry is what makes a forwarded link stop working.
    Never cached and never stored: the signature is part of the URL, so a
    persisted one is a persisted grant.
    """
    cloudinary, settings = _client()
    import time

    ttl = ttl_seconds or settings.signed_url_ttl_seconds
    url, _ = cloudinary.utils.cloudinary_url(
        f"{settings.folder}/{key}",
        resource_type="raw",
        type="authenticated",
        sign_url=True,
        expires_at=int(time.time()) + ttl,
    )
    return url


def delete_object(key: str) -> bool:
    """Remove one asset. False when it was already gone.

    Never raises: a delete is called from teardown paths (detach, the TTL
    sweep, a failed upload's rollback) where a storage failure must not undo
    the database change that is the actual record of removal. A leaked object
    is visible in a prefix listing; a row that would not delete is not.
    """
    try:
        cloudinary, settings = _client()
        result = cloudinary.uploader.destroy(
            f"{settings.folder}/{key}",
            resource_type="raw",
            type="authenticated",
            invalidate=True,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Cloudinary: could not delete %s", key, exc_info=True)
        return False
    return result.get("result") == "ok"
