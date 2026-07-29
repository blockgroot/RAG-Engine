"""Stale-conversation cleanup (``app/memory/pg_store.py::delete_stale_conversations``).

DB-backed, no LLM needed — this only exercises the deletion SQL, not
retrieval/generation. Proves cleanup is based on last ACTIVITY (most recent
turn), never plain creation time, so a long-running but still-active
conversation is never deleted just because it started long ago.
"""

from __future__ import annotations

import uuid

from app.db.connection import get_connection
from app.memory import delete_stale_conversations

from .conftest import requires_db


def _backdate_conversation(conversation_id: str, days_ago: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE conversations SET created_at = now() - make_interval(days => %s) "
            "WHERE id = %s::uuid",
            (days_ago, conversation_id),
        )


def _backdate_turns(conversation_id: str, days_ago: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE conversation_turns SET created_at = now() - make_interval(days => %s) "
            "WHERE conversation_id = %s::uuid",
            (days_ago, conversation_id),
        )


def _exists(conversation_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = %s::uuid", (conversation_id,)
        ).fetchone()
    return row is not None


@requires_db
def test_deletes_conversation_inactive_past_retention(store, memory, org_cleanup):
    org_id = store.create_organization(f"Cleanup Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    stale = memory.create_conversation(org_id)
    memory.append_turn(stale, "old question", "old answer")
    _backdate_conversation(stale, 100)
    _backdate_turns(stale, 100)

    deleted = delete_stale_conversations(90)

    assert deleted >= 1
    assert not _exists(stale)


@requires_db
def test_keeps_conversation_created_long_ago_but_recently_active(store, memory, org_cleanup):
    """Activity, not creation time, decides staleness."""
    org_id = store.create_organization(f"Cleanup Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    still_active = memory.create_conversation(org_id)
    memory.append_turn(still_active, "first question", "first answer")
    _backdate_conversation(still_active, 100)  # created long ago...
    _backdate_turns(still_active, 100)
    # ...but has a turn from today (e.g. a follow-up in an old-but-ongoing chat).
    memory.append_turn(still_active, "recent question", "recent answer")

    delete_stale_conversations(90)

    assert _exists(still_active)


@requires_db
def test_deletes_old_conversation_with_no_turns_at_all(store, memory, org_cleanup):
    """No turns to check activity from -> falls back to creation time."""
    org_id = store.create_organization(f"Cleanup Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    empty = memory.create_conversation(org_id)
    _backdate_conversation(empty, 100)

    delete_stale_conversations(90)

    assert not _exists(empty)


@requires_db
def test_keeps_recent_conversation(store, memory, org_cleanup):
    org_id = store.create_organization(f"Cleanup Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    recent = memory.create_conversation(org_id)
    memory.append_turn(recent, "question", "answer")

    delete_stale_conversations(90)

    assert _exists(recent)
