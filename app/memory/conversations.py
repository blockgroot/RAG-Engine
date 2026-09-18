"""Listing, reading and deleting a person's own conversations.

Separate from `pg_store.ConversationStore`, which is the RAG pipeline's
working memory (summary + recent turns, keyed only by `conversation_id`).
This module answers the product's questions instead -- "which chats are mine?",
"show me that one", "delete it" -- and every one of them is scoped
`(org_id, user_id, workspace_id)`, the shape `schedulers` and `insight_pins`
already use for a personal surface.

The split matters: the pipeline must not learn about users, and this must not
be reachable without one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..db.connection import get_connection

#: How long a conversation survives. Deliberately the same number the UI
#: states ("kept for 30 days"): a retention promise nobody can read is not a
#: promise. The sweep IS the list length -- there is no separate display cap,
#: because two numbers that must agree eventually do not (CLAUDE.md records
#: exactly that failure for FREQUENCIES/_FREQUENCY_INTERVAL/_FIRST_WINDOW),
#: and a chat alive but off the end of a capped list is unreachable and
#: undeletable, which is the bug this feature exists to remove.
DEFAULT_CONVERSATION_TTL_DAYS = 30


@dataclass(frozen=True)
class ConversationSummaryRow:
    id: str
    #: The first question asked, used as the title. No LLM and no column: a
    #: generated title is a second thing to keep true, and the first question
    #: is what people actually recognise a chat by.
    title: str | None
    turn_count: int
    attachment_count: int
    created_at: datetime
    last_activity_at: datetime


@dataclass(frozen=True)
class ConversationTurnRow:
    turn_index: int
    question: str
    answer: str
    created_at: datetime


def list_conversations(
    *, org_id: str, user_id: str, workspace_id: str | None
) -> list[ConversationSummaryRow]:
    """This person's chats in this scope, most recently used first.

    Every surviving conversation, not a capped window -- see
    ``DEFAULT_CONVERSATION_TTL_DAYS``.

    Empty chats are omitted: `ensureConversation` creates a row before the
    first question is sent, so a refresh or an abandoned visit leaves one with
    nothing in it, and a list of blank entries is worse than a short list. They
    are still swept on the TTL like any other.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id::text,
                   (SELECT t.question FROM conversation_turns t
                     WHERE t.conversation_id = c.id
                     ORDER BY t.turn_index LIMIT 1),
                   (SELECT count(*) FROM conversation_turns t
                     WHERE t.conversation_id = c.id),
                   (SELECT count(*) FROM conversation_attachments a
                     WHERE a.conversation_id = c.id),
                   c.created_at,
                   coalesce(
                       (SELECT max(t.created_at) FROM conversation_turns t
                         WHERE t.conversation_id = c.id),
                       c.created_at
                   ) AS last_activity_at
            FROM conversations c
            WHERE c.org_id = %s::uuid
              AND c.user_id = %s::uuid
              AND c.workspace_id IS NOT DISTINCT FROM %s::uuid
              AND EXISTS (SELECT 1 FROM conversation_turns t
                           WHERE t.conversation_id = c.id)
            ORDER BY last_activity_at DESC
            """,
            (org_id, user_id, workspace_id),
        ).fetchall()
    return [
        ConversationSummaryRow(r[0], r[1], int(r[2]), int(r[3]), r[4], r[5])
        for r in rows
    ]


def get_conversation_turns(
    *, conversation_id: str, org_id: str, user_id: str, workspace_id: str | None
) -> list[ConversationTurnRow]:
    """The full transcript, oldest first.

    Full because the fold stopped deleting: turns beyond the memory window are
    marked (`conversations.folded_through`), not removed, so what is returned
    here is the whole conversation rather than the tail the model still reads.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.turn_index, t.question, t.answer, t.created_at
            FROM conversation_turns t
            JOIN conversations c ON c.id = t.conversation_id
            WHERE t.conversation_id = %s::uuid
              AND c.org_id = %s::uuid
              AND c.user_id = %s::uuid
              AND c.workspace_id IS NOT DISTINCT FROM %s::uuid
            ORDER BY t.turn_index
            """,
            (conversation_id, org_id, user_id, workspace_id),
        ).fetchall()
    return [ConversationTurnRow(int(r[0]), r[1], r[2], r[3]) for r in rows]


def delete_conversation(
    *, conversation_id: str, org_id: str, user_id: str, workspace_id: str | None
) -> bool:
    """Delete one chat. True when a row went; False is a 404 for the caller.

    Turns, attachments and the last-retrieval row go with it by cascade, so
    "delete this chat" removes the uploaded file too -- which is what someone
    pressing it means, and is why the attachment sweep is a backstop rather
    than the mechanism.

    `user_id` is in the WHERE clause even though a conversation is already
    personal: this is the one destructive route a member can call, and a
    missing predicate here deletes someone else's chat rather than failing.
    """
    with get_connection() as conn:
        row = conn.execute(
            "DELETE FROM conversations "
            "WHERE id = %s::uuid AND org_id = %s::uuid AND user_id = %s::uuid "
            "AND workspace_id IS NOT DISTINCT FROM %s::uuid "
            "RETURNING 1",
            (conversation_id, org_id, user_id, workspace_id),
        ).fetchone()
    return row is not None


def purge_expired_conversations(
    ttl_days: int = DEFAULT_CONVERSATION_TTL_DAYS,
) -> int:
    """Delete conversations past the retention horizon. Returns how many.

    Keyed on last ACTIVITY, not creation: a chat someone returned to yesterday
    is not 30 days old just because it started then.

    A NULL `user_id` row predates the owner column and has no owner to keep it
    for, so it ages out on the same clock rather than living forever.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            DELETE FROM conversations c
            WHERE coalesce(
                    (SELECT max(t.created_at) FROM conversation_turns t
                      WHERE t.conversation_id = c.id),
                    c.created_at
                  ) < now() - make_interval(days => %s)
            RETURNING 1
            """,
            (ttl_days,),
        ).fetchall()
    return len(rows)
