"""The object store for attachments: original bytes plus cached plaintext.

The text is a second object, so a large PDF is not re-parsed on every turn.
The database keeps metadata only. Every asset is `authenticated` and every
URL handed out is signed and short-lived: a default public URL would outlive
the chat and work for anyone it is forwarded to.
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

    #: Cloudinary's `public_id`; never a URL (a persisted URL is a grant).
    key: str
    bytes_written: int


def plaintext_key(key: str) -> str:
    """The companion object holding this upload's extracted text, beside the
    original under one prefix.
    """
    return f"{key}__plaintext"


def configured() -> bool:
    """False when no object store is set up: the caller keeps text in Postgres."""
    return CloudinarySettings.from_env().configured


def _client():
    """The configured Cloudinary SDK (imported lazily), or a raise naming
    what is missing.
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

    ``raw`` so a PDF is not treated as an image; `authenticated`, so a
    guessed public_id is useless without a signature.
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
            # The member-supplied filename never shapes the key or a URL.
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
    """Fetch one asset. Raises rather than returning empty, which would read
    as "this document says nothing".
    """
    cloudinary, settings = _client()
    import httpx

    url = _delivery_url(key)
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


def _delivery_url(key: str) -> str:
    """A signed CDN URL, used inside this process and never handed out.

    Its signature never expires (``expires_at`` is ignored), which is safe
    only because it never leaves the process. CDN, not the API endpoint: this
    is the per-turn read path and must not hit an API rate limit.
    """
    cloudinary, settings = _client()
    url, _ = cloudinary.utils.cloudinary_url(
        f"{settings.folder}/{key}",
        resource_type="raw",
        type="authenticated",
        sign_url=True,
    )
    return url


def signed_url(key: str, *, ttl_seconds: int | None = None) -> str:
    """An expiring URL, for handing to a browser.

    `private_download_url`, whose signature carries the expiry (unlike
    `_delivery_url`'s). Never stored; build it per request.
    """
    cloudinary, settings = _client()
    import time

    ttl = ttl_seconds if ttl_seconds is not None else settings.signed_url_ttl_seconds
    return cloudinary.utils.private_download_url(
        f"{settings.folder}/{key}",
        None,  # the key carries no extension
        resource_type="raw",
        type="authenticated",
        expires_at=int(time.time()) + ttl,
    )


def delete_object(key: str) -> bool:
    """Remove one asset. False when it was already gone.

    Never raises: called from teardown, where a storage failure must not undo
    the row delete. A leaked object shows in a prefix listing.
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


def list_keys(prefix: str = "") -> list[str]:
    """Every key this app has written under ``folder``/``prefix``, to find
    orphans. Paginated: a silent first page of 500 would look complete.
    """
    cloudinary, settings = _client()
    full_prefix = f"{settings.folder}/{prefix}" if prefix else f"{settings.folder}/"
    keys: list[str] = []
    cursor = None
    try:
        while True:
            page = cloudinary.api.resources(
                type="authenticated",
                resource_type="raw",
                prefix=full_prefix,
                max_results=500,
                next_cursor=cursor,
            )
            for resource in page.get("resources", []):
                public_id = resource.get("public_id", "")
                keys.append(public_id[len(settings.folder) + 1 :] if public_id.startswith(settings.folder + "/") else public_id)
            cursor = page.get("next_cursor")
            if not cursor:
                break
    except Exception as exc:  # noqa: BLE001
        raise AttachmentStorageError("Could not list stored attachments", cause=exc) from exc
    return keys
