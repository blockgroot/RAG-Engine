"""Read/write the extracted text of a conversation's attachments.

Every function takes ``org_id``, ``conversation_id`` AND ``user_id`` and puts
all three in the WHERE clause. That is one more than the caller strictly needs
-- the route has already checked the conversation belongs to this person --
and it is deliberate: an attachment is the only content in this system a
MEMBER supplies, so the check that keeps it private is not one anybody should
have to remember to call. A missing predicate here leaks rather than fails,
which is the same reasoning `schedulers` and `insight_pins` are scoped on
``(org_id, user_id)``.
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
) -> Attachment:
    """Store one attachment: metadata here, bytes and text in the object store.

    The order matters and is not arbitrary. The row is INSERTed first because
    Postgres mints the id the object keys are derived from, then both assets
    are uploaded, then the row is stamped with its key. If an upload fails the
    row is deleted and the failure is raised -- a row pointing at objects that
    do not exist would read as an empty document on every later question,
    which is a claim about the file rather than about the upload.

    ``data`` is the original bytes. Optional only so a caller that has already
    discarded them (or a test) can store text alone; when it is absent the
    plaintext asset is still written, so answering works and only the
    "download the original" affordance is missing.
    """
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO conversation_attachments "
            "(conversation_id, org_id, user_id, filename, content_type, "
            " content, char_count, truncated) "
            "VALUES (%s, %s, %s, %s, %s, NULL, %s, %s) "
            "RETURNING id::text",
            (
                conversation_id,
                org_id,
                user_id,
                filename,
                content_type,
                len(content),
                truncated,
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
        # Roll the row back BEFORE re-raising, and clean up a half-written
        # pair: a partial upload that survives is an orphan nothing will ever
        # list, because the row that named it is gone.
        blobstore.delete_object(blobstore.plaintext_key(attachment_id))
        blobstore.delete_object(attachment_id)
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM conversation_attachments WHERE id = %s",
                (attachment_id,),
            )
        raise

    return Attachment(attachment_id, filename, len(content), truncated)


def list_attachments(
    *, org_id: str, conversation_id: str, user_id: str
) -> list[Attachment]:
    """Metadata only -- the UI never needs the text, and not selecting it
    keeps a 60k-char blob out of every page render."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id::text, filename, char_count, truncated "
            "FROM conversation_attachments "
            "WHERE conversation_id = %s AND org_id = %s AND user_id = %s "
            "ORDER BY created_at",
            (conversation_id, org_id, user_id),
        ).fetchall()
    return [Attachment(r[0], r[1], r[2], r[3]) for r in rows]


def load_attachment_texts(
    *, org_id: str, conversation_id: str, user_id: str
) -> list[Attachment]:
    """The attachments WITH their text, oldest first, for the prompt.

    Three sources, in the order Onyx reads them
    (`chat_utils::_get_or_extract_plaintext`):

    1. the cached PLAINTEXT asset -- the steady-state path, one small fetch
       and no parsing, which is the whole reason that asset is written;
    2. the `content` column -- rows that predate the move to the object store,
       which must keep answering;
    3. the ORIGINAL bytes, re-extracted -- a plaintext asset that was lost or
       never written.

    A file whose text cannot be recovered by any of the three is OMITTED
    rather than included empty: an empty context reaches the prompt as "this
    document says nothing", which the model will answer from.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id::text, filename, char_count, truncated, content, storage_key "
            "FROM conversation_attachments "
            "WHERE conversation_id = %s AND org_id = %s AND user_id = %s "
            "ORDER BY created_at",
            (conversation_id, org_id, user_id),
        ).fetchall()

    loaded: list[Attachment] = []
    for attachment_id, filename, char_count, truncated, content, storage_key in rows:
        text = _resolve_text(attachment_id, filename, content, storage_key)
        if text:
            loaded.append(Attachment(attachment_id, filename, char_count, truncated, text))
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
    """True when a row was actually removed. False is a 404 for the caller --
    never a 403, which would confirm the id exists to someone guessing."""
    with get_connection() as conn:
        row = conn.execute(
            "DELETE FROM conversation_attachments "
            "WHERE id = %s AND conversation_id = %s AND org_id = %s "
            "AND user_id = %s RETURNING storage_key",
            (attachment_id, conversation_id, org_id, user_id),
        ).fetchone()
    if row is None:
        return False
    _drop_objects([row[0]])
    return True


def _drop_objects(keys: list[str | None]) -> None:
    """Best-effort removal of the stored pair for each key.

    Runs AFTER the database delete and never raises: the row is the record of
    removal, so a storage failure must not resurrect an attachment the member
    has already been told is gone. `delete_object` logs; a leaked asset shows
    up in a prefix listing.
    """
    for key in keys:
        if not key:
            continue
        blobstore.delete_object(blobstore.plaintext_key(key))
        blobstore.delete_object(key)


#: How long an attachment's text outlives its last use. Attachments are the
#: only content here a MEMBER can create without an admin, on a 500MB
#: database, and nothing in this codebase deletes a conversation -- so
#: without a sweep they accumulate for the life of the deployment. Generous
#: because the cost of expiring one early is a re-upload, while the cost of
#: never expiring is a full database.
DEFAULT_ATTACHMENT_TTL_HOURS = 30 * 24

#: The much shorter clock for an attachment whose conversation NEVER asked a
#: question. The chat UI holds `conversationId` in a React ref, so a browser
#: refresh silently starts a new conversation and the old one -- with its
#: files -- becomes unreachable: no history list, no way back to it. Measured
#: on the live database, 37 of 114 conversations (32%) were created and never
#: used, so this is the common case, not an edge one. Holding that text for
#: the full TTL keeps a file alive for weeks after the person believes their
#: chat is gone, which is a privacy claim we should not have to make.
#:
#: A DAY rather than minutes: a conversation with no turns is also what a
#: person mid-upload has, and deleting the file out from under someone who is
#: still typing their first question would be far worse than keeping it a few
#: hours too long.
DEFAULT_UNUSED_ATTACHMENT_TTL_HOURS = 24


def purge_expired_attachments(
    ttl_hours: int = DEFAULT_ATTACHMENT_TTL_HOURS,
    unused_ttl_hours: int = DEFAULT_UNUSED_ATTACHMENT_TTL_HOURS,
) -> int:
    """Drop attachment TEXT that has aged out. Returns how many.

    Two clocks in one statement, because they are the same sweep with
    different deadlines:

    1. ``ttl_hours`` -- any attachment, however used.
    2. ``unused_ttl_hours`` -- an attachment whose conversation has NO turns.
       That conversation was abandoned before a single question, which on
       this frontend is what a page refresh produces.

    Keyed on ``created_at``: the row never changes after insert, so its own
    age is the only honest clock available.

    Deletes the ROW, so an expired file simply stops being attached -- the
    chat and its turns are untouched, and the next question answers from the
    connected sources again. That is the same outcome as the member pressing
    the x, which is why nothing extra has to be explained to them.
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
    # The sweep is the only thing that would ever remove these assets, so an
    # expired row whose objects are left behind is storage nothing will ever
    # reclaim -- the failure mode an object store has and a TEXT column does
    # not.
    _drop_objects([r[0] for r in rows])
    return len(rows)
