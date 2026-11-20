"""Stored upload files are deleted whenever their row goes, by any path, and a
failed deletion is retried, never lost. Only keys that were ours."""

from __future__ import annotations

import uuid

from app.attachments import blobstore, store as attachment_store
from app.db.connection import get_connection

from .conftest import requires_db


def _chat_with_upload(org_id: str) -> tuple[str, str]:
    key = f"test-{uuid.uuid4().hex}"
    with get_connection() as conn:
        user_id = conn.execute(
            "INSERT INTO users (email, org_id) VALUES (%s, %s::uuid) RETURNING id::text",
            (f"u-{uuid.uuid4().hex[:8]}@example.com", org_id),
        ).fetchone()[0]
        conversation_id = conn.execute(
            "INSERT INTO conversations (org_id) VALUES (%s::uuid) RETURNING id::text", (org_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO conversation_attachments (conversation_id, org_id, user_id, filename, "
            "content_type, content, char_count, storage_key) "
            "VALUES (%s::uuid, %s::uuid, %s::uuid, 'a.pdf', 'application/pdf', '', 0, %s)",
            (conversation_id, org_id, user_id, key),
        )
    return conversation_id, key


def _queued(key: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT attempts FROM storage_deletions WHERE storage_key = %s", (key,)
        ).fetchone()


@requires_db
def test_deleting_a_chat_queues_its_files_and_upkeep_deletes_them(store, org_cleanup, monkeypatch):
    """A cascade (chat, user or company deleted) left its files in storage for good."""
    org_id = store.create_organization(f"Storage {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    conversation_id, key = _chat_with_upload(org_id)
    with get_connection() as conn:
        conn.execute("DELETE FROM conversations WHERE id = %s::uuid", (conversation_id,))
    assert _queued(key) == (0,)

    removed: list[str] = []
    monkeypatch.setattr(blobstore, "configured", lambda: True)
    monkeypatch.setattr(blobstore, "remove_object", lambda k: removed.append(k) or False)
    attachment_store.drain_storage_deletions(limit=10_000)
    assert _queued(key) == (1,), "a failure stays queued, counted"

    # Only this test's key reports gone: the shared queue may hold real keys,
    # which must stay queued (a False only counts an attempt).
    monkeypatch.setattr(blobstore, "remove_object",
                        lambda k: removed.append(k) or k.startswith(key))
    attachment_store.drain_storage_deletions(limit=10_000)
    assert _queued(key) is None
    assert key in removed and blobstore.plaintext_key(key) in removed


@requires_db
def test_an_immediate_removal_clears_the_queue(store, org_cleanup, monkeypatch):
    org_id = store.create_organization(f"Storage {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    conversation_id, key = _chat_with_upload(org_id)
    with get_connection() as conn:
        conn.execute("DELETE FROM conversation_attachments WHERE storage_key = %s", (key,))
    monkeypatch.setattr(blobstore, "remove_object", lambda k: True)
    attachment_store.drop_stored(key)
    assert _queued(key) is None
