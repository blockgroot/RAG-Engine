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

from dataclasses import dataclass

from ..db.connection import get_connection


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
) -> Attachment:
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO conversation_attachments "
            "(conversation_id, org_id, user_id, filename, content_type, "
            " content, char_count, truncated) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
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
            ),
        ).fetchone()
    return Attachment(row[0], filename, len(content), truncated)


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
    """The attachments WITH their text, oldest first, for the prompt."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id::text, filename, char_count, truncated, content "
            "FROM conversation_attachments "
            "WHERE conversation_id = %s AND org_id = %s AND user_id = %s "
            "ORDER BY created_at",
            (conversation_id, org_id, user_id),
        ).fetchall()
    return [Attachment(r[0], r[1], r[2], r[3], r[4]) for r in rows]


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
            "AND user_id = %s RETURNING 1",
            (attachment_id, conversation_id, org_id, user_id),
        ).fetchone()
    return row is not None


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
            RETURNING 1
            """,
            (ttl_hours, unused_ttl_hours),
        ).fetchall()
    return len(rows)
