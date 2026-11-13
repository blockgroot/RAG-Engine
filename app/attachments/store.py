"""Read/write the extracted text of a conversation's attachments.

Every query puts ``org_id``, ``conversation_id`` and ``user_id`` in the WHERE,
even though the route already checked ownership: member-supplied content must
not depend on a check someone remembers to call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..db.connection import get_connection
from . import blobstore
from .blobstore import AttachmentStorageError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Attachment:
    id: str
    filename: str
    char_count: int
    truncated: bool
    #: Absent on a listing (``list_attachments``), present when the pipeline
    #: asks for the text it is about to put in a prompt.
    content: str | None = None
    #: Prompt-injection probability from upload; None = unscored.
    injection_score: float | None = None


def save_attachment(
    *,
    org_id: str,
    conversation_id: str,
    user_id: str,
    filename: str,
    content_type: str,
    content: str,
    truncated: bool,
    data: bytes | None = None,
    injection_score: float | None = None,
) -> Attachment:
    """Store one attachment: metadata here, bytes and text in the object store.

    Insert the row (it mints the key), upload, then stamp the key. A failed
    upload deletes the row and raises, or it would read as an empty file.
    ``data`` (original bytes) is optional; without it only the download is
    missing. With no object store, the text goes in ``content``.
    """
    if not blobstore.configured():
        with get_connection() as conn:
            row = conn.execute(
                "INSERT INTO conversation_attachments "
                "(conversation_id, org_id, user_id, filename, content_type, "
                " content, char_count, truncated, injection_score) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id::text",
                (
                    conversation_id,
                    org_id,
                    user_id,
                    filename,
                    content_type,
                    content,
                    len(content),
                    truncated,
                    injection_score,
                ),
            ).fetchone()
        return Attachment(
            row[0], filename, len(content), truncated, injection_score=injection_score
        )

    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO conversation_attachments "
            "(conversation_id, org_id, user_id, filename, content_type, "
            " content, char_count, truncated, injection_score) "
            "VALUES (%s, %s, %s, %s, %s, NULL, %s, %s, %s) "
            "RETURNING id::text",
            (
                conversation_id,
                org_id,
                user_id,
                filename,
                content_type,
                len(content),
                truncated,
                injection_score,
            ),
        ).fetchone()
    attachment_id = row[0]

    try:
        blobstore.save_text(blobstore.plaintext_key(attachment_id), content)
        if data is not None:
            blobstore.save_bytes(
                attachment_id, data, content_type=content_type
            )
        with get_connection() as conn:
            conn.execute(
                "UPDATE conversation_attachments SET storage_key = %s "
                "WHERE id = %s",
                (attachment_id, attachment_id),
            )
    except AttachmentStorageError:
        # Roll back the row and any half-written pair, or it is an orphan.
        blobstore.delete_object(blobstore.plaintext_key(attachment_id))
        blobstore.delete_object(attachment_id)
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM conversation_attachments WHERE id = %s",
                (attachment_id,),
            )
        raise

    return Attachment(
        attachment_id, filename, len(content), truncated, injection_score=injection_score
    )


def list_attachments(
    *, org_id: str, conversation_id: str, user_id: str
) -> list[Attachment]:
    """Metadata only -- the UI never needs the text, and not selecting it
    keeps a 60k-char blob out of every page render."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id::text, filename, char_count, truncated, injection_score "
            "FROM conversation_attachments "
            "WHERE conversation_id = %s AND org_id = %s AND user_id = %s "
            "ORDER BY created_at",
            (conversation_id, org_id, user_id),
        ).fetchall()
    return [Attachment(r[0], r[1], r[2], r[3], injection_score=r[4]) for r in rows]


def load_attachment_texts(
    *, org_id: str, conversation_id: str, user_id: str
) -> list[Attachment]:
    """The attachments with their text, oldest first, for the prompt.

    Text from the cached plaintext asset, else the `content` column, else the
    original bytes re-extracted. A file with none is omitted, never sent
    empty (the model would answer "it says nothing").
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id::text, filename, char_count, truncated, content, storage_key, "
            "injection_score "
            "FROM conversation_attachments "
            "WHERE conversation_id = %s AND org_id = %s AND user_id = %s "
            "ORDER BY created_at",
            (conversation_id, org_id, user_id),
        ).fetchall()

    loaded: list[Attachment] = []
    for attachment_id, filename, char_count, truncated, content, storage_key, score in rows:
        text = _resolve_text(attachment_id, filename, content, storage_key)
        if text:
            loaded.append(
                Attachment(attachment_id, filename, char_count, truncated, text, score)
            )
    return loaded


def _resolve_text(
    attachment_id: str, filename: str, content: str | None, storage_key: str | None
) -> str | None:
    """Text for one attachment, or None when nothing could be recovered."""
    if storage_key:
        try:
            return blobstore.read_text(blobstore.plaintext_key(storage_key))
        except AttachmentStorageError:
            logger.warning(
                "Attachments: plaintext missing for %s, falling back",
                attachment_id,
                exc_info=True,
            )

    if content:
        return content

    if storage_key:
        # Last resort: re-parse the original. Expensive, which is exactly why
        # the plaintext asset exists -- reaching here means that asset is gone.
        try:
            from ..config.settings import AttachmentSettings
            from .extract import extract_text

            limits = AttachmentSettings.from_env()
            raw = blobstore.read_bytes(storage_key)
            text, _ = extract_text(
                filename,
                raw,
                max_chars=limits.max_chars,
                max_pdf_pages=limits.max_pdf_pages,
                max_csv_rows=limits.max_csv_rows,
            )
            return text
        except Exception:  # noqa: BLE001
            logger.warning(
                "Attachments: could not recover text for %s", attachment_id, exc_info=True
            )

    return None


def count_attachments(*, org_id: str, conversation_id: str, user_id: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT count(*) FROM conversation_attachments "
            "WHERE conversation_id = %s AND org_id = %s AND user_id = %s",
            (conversation_id, org_id, user_id),
        ).fetchone()
    return int(row[0])


def delete_attachment(
    *, attachment_id: str, org_id: str, conversation_id: str, user_id: str
) -> bool:
    """True when a row was actually removed, with its stored objects."""
    removed, key = remove_attachment_row(
        attachment_id=attachment_id, org_id=org_id,
        conversation_id=conversation_id, user_id=user_id,
    )
    if removed:
        drop_stored(key)
    return removed


def remove_attachment_row(
    *, attachment_id: str, org_id: str, conversation_id: str, user_id: str
) -> tuple[bool, str | None]:
    """Delete the row only: ``(removed, storage_key)``.

    The row is the removal (tables cascade); ``drop_stored`` runs after the
    response, so a slow purge never reads as "couldn't remove".
    """
    with get_connection() as conn:
        row = conn.execute(
            "DELETE FROM conversation_attachments "
            "WHERE id = %s AND conversation_id = %s AND org_id = %s "
            "AND user_id = %s RETURNING storage_key",
            (attachment_id, conversation_id, org_id, user_id),
        ).fetchone()
    return (row is not None, row[0] if row else None)


def drop_stored(key: str | None) -> None:
    """Remove one attachment's stored pair. Never raises."""
    _drop_objects([key])


def _drop_objects(keys: list[str | None]) -> None:
    """Best-effort removal of the stored pair for each key, after the row
    delete. Never raises; a leaked asset shows in a prefix listing.
    """
    for key in keys:
        if not key:
            continue
        blobstore.delete_object(blobstore.plaintext_key(key))
        blobstore.delete_object(key)


#: How long an attachment lives. Generous: expiring early costs a re-upload.
#: Conversation retention also removes them (cascade).
DEFAULT_ATTACHMENT_TTL_HOURS = 30 * 24

#: Shorter clock for a conversation that never asked a question (often an
#: abandoned one). A day, not minutes: it is also what someone mid-upload has.
DEFAULT_UNUSED_ATTACHMENT_TTL_HOURS = 24


def purge_expired_attachments(
    ttl_hours: int = DEFAULT_ATTACHMENT_TTL_HOURS,
    unused_ttl_hours: int = DEFAULT_UNUSED_ATTACHMENT_TTL_HOURS,
) -> int:
    """Drop attachments that have aged out. Returns how many.

    Two clocks on ``created_at``: ``ttl_hours`` for any, ``unused_ttl_hours``
    when the conversation has no turns. Deletes the row, as pressing the x
    does; the chat is untouched.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            DELETE FROM conversation_attachments a
            WHERE a.created_at < now() - make_interval(hours => %s)
               OR (
                    a.created_at < now() - make_interval(hours => %s)
                    AND NOT EXISTS (
                        SELECT 1 FROM conversation_turns t
                        WHERE t.conversation_id = a.conversation_id
                    )
               )
            RETURNING a.storage_key
            """,
            (ttl_hours, unused_ttl_hours),
        ).fetchall()
    # Nothing else would ever reclaim these assets.
    _drop_objects([r[0] for r in rows])
    return len(rows)


def list_orphan_objects() -> list[str]:
    """Stored keys with no row pointing at them. Reports, never deletes.

    Another deployment sharing the folder would look orphaned too; each
    environment needs its own `CLOUDINARY_FOLDER`.
    """
    known: set[str] = set()
    with get_connection() as conn:
        for (key,) in conn.execute(
            "SELECT storage_key FROM conversation_attachments "
            "WHERE storage_key IS NOT NULL"
        ).fetchall():
            known.add(key)
            known.add(blobstore.plaintext_key(key))

    return sorted(key for key in blobstore.list_keys() if key not in known)
