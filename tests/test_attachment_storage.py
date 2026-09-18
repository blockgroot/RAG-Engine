"""Attachment bytes and text live in the object store; Postgres keeps the row.

What is pinned here is the part that can silently lose a member's file: the
ORDER of the three text sources, and the rollback when storage fails. A row
pointing at objects that do not exist answers every later question as an empty
document, which is a claim about the file rather than about the upload -- so a
failed upload must leave no row at all.

The object store is faked. These tests are about this module's decisions, not
about Cloudinary's API, and a test that needs credentials is a test nobody runs.
"""

from __future__ import annotations

import uuid

import pytest

from app.attachments import store as attach_store
from app.attachments.blobstore import AttachmentStorageError
from app.db import get_connection
from .conftest import requires_db

pytestmark = requires_db


class FakeBlobStore:
    """An in-memory stand-in with the four calls `store.py` makes."""

    def __init__(self, fail_on: str | None = None):
        self.objects: dict[str, bytes] = {}
        self.fail_on = fail_on
        self.deleted: list[str] = []

    # -- the surface store.py uses -------------------------------------
    def plaintext_key(self, key: str) -> str:
        return f"{key}__plaintext"

    def save_text(self, key: str, text: str):
        if self.fail_on == "text":
            raise AttachmentStorageError("storage down")
        self.objects[key] = text.encode("utf-8")

    def save_bytes(self, key: str, data: bytes, *, content_type=None):
        if self.fail_on == "bytes":
            raise AttachmentStorageError("storage down")
        self.objects[key] = data

    def read_text(self, key: str) -> str:
        if key not in self.objects:
            raise AttachmentStorageError(f"missing {key}")
        return self.objects[key].decode("utf-8")

    def read_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise AttachmentStorageError(f"missing {key}")
        return self.objects[key]

    def delete_object(self, key: str) -> bool:
        self.deleted.append(key)
        return self.objects.pop(key, None) is not None


@pytest.fixture
def blob(monkeypatch):
    fake = FakeBlobStore()
    monkeypatch.setattr(attach_store, "blobstore", fake)
    return fake


@pytest.fixture
def scope(org_cleanup):
    """A throwaway org + user + conversation to hang attachments on."""
    with get_connection() as conn:
        org = str(
            conn.execute(
                "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
                (f"attach-{uuid.uuid4().hex[:8]}",),
            ).fetchone()[0]
        )
        user = str(
            conn.execute(
                "INSERT INTO users (email, org_id, role) VALUES (%s, %s, 'member') "
                "RETURNING id",
                (f"{uuid.uuid4().hex[:8]}@example.com", org),
            ).fetchone()[0]
        )
        conv = str(
            conn.execute(
                "INSERT INTO conversations (org_id, user_id) VALUES (%s, %s) "
                "RETURNING id",
                (org, user),
            ).fetchone()[0]
        )
        conn.commit()
    org_cleanup.append(org)
    return {"org_id": org, "user_id": user, "conversation_id": conv}


def _save(scope, blob, *, text="the notice period is 30 days", data=b"%PDF-1.4 fake"):
    return attach_store.save_attachment(
        **scope,
        filename="contract.pdf",
        content_type="application/pdf",
        content=text,
        truncated=False,
        data=data,
    )


def test_text_and_bytes_both_land_in_the_object_store(scope, blob):
    saved = _save(scope, blob)

    assert blob.objects[f"{saved.id}__plaintext"].decode() == (
        "the notice period is 30 days"
    )
    assert blob.objects[saved.id] == b"%PDF-1.4 fake"

    # Postgres keeps the metadata and the key -- never the text.
    with get_connection() as conn:
        row = conn.execute(
            "SELECT content, storage_key, char_count FROM conversation_attachments "
            "WHERE id = %s",
            (saved.id,),
        ).fetchone()
    assert row[0] is None
    assert row[1] == saved.id
    assert row[2] == len("the notice period is 30 days")


def test_the_prompt_reads_the_cached_plaintext(scope, blob):
    _save(scope, blob)
    loaded = attach_store.load_attachment_texts(**scope)
    assert [a.content for a in loaded] == ["the notice period is 30 days"]


def test_a_legacy_row_still_answers(scope, blob):
    """A row written before the move has its text in the column and no key."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO conversation_attachments "
            "(conversation_id, org_id, user_id, filename, content_type, "
            " content, char_count, truncated) "
            "VALUES (%s, %s, %s, 'old.txt', 'text/plain', %s, 8, false)",
            (
                scope["conversation_id"],
                scope["org_id"],
                scope["user_id"],
                "old text",
            ),
        )
        conn.commit()

    loaded = attach_store.load_attachment_texts(**scope)
    assert [a.content for a in loaded] == ["old text"]


def test_a_lost_plaintext_falls_back_to_the_original(scope, blob):
    """The expensive path: re-extract from the bytes we still hold.

    A .txt so the assertion is about the FALLBACK firing, not about a parser:
    the re-extraction runs the real `extract_text`, which would reject bytes
    that are not actually the format the filename claims.
    """
    saved = attach_store.save_attachment(
        **scope,
        filename="notes.txt",
        content_type="text/plain",
        content="hello from the file",
        truncated=False,
        data=b"hello from the file",
    )
    del blob.objects[f"{saved.id}__plaintext"]

    loaded = attach_store.load_attachment_texts(**scope)
    assert "hello from the file" in (loaded[0].content or "")


def test_an_unrecoverable_attachment_is_omitted_not_empty(scope, blob):
    """An empty context reaches the prompt as 'this document says nothing'."""
    saved = _save(scope, blob)
    blob.objects.clear()

    assert attach_store.load_attachment_texts(**scope) == []
    # The row is untouched -- losing the text must not silently delete the
    # member's record of having attached something.
    assert attach_store.count_attachments(**scope) == 1
    assert saved.id


def test_a_failed_upload_leaves_no_row(scope, monkeypatch):
    """Otherwise the row points at nothing and answers as an empty document."""
    fake = FakeBlobStore(fail_on="text")
    monkeypatch.setattr(attach_store, "blobstore", fake)

    with pytest.raises(AttachmentStorageError):
        _save(scope, fake)

    assert attach_store.count_attachments(**scope) == 0


def test_a_failed_byte_upload_also_rolls_back_the_text(scope, monkeypatch):
    """A half-written pair is an orphan nothing will ever list."""
    fake = FakeBlobStore(fail_on="bytes")
    monkeypatch.setattr(attach_store, "blobstore", fake)

    with pytest.raises(AttachmentStorageError):
        _save(scope, fake)

    assert attach_store.count_attachments(**scope) == 0
    assert fake.objects == {}


def test_detaching_removes_both_objects(scope, blob):
    saved = _save(scope, blob)

    assert attach_store.delete_attachment(
        attachment_id=saved.id,
        org_id=scope["org_id"],
        conversation_id=scope["conversation_id"],
        user_id=scope["user_id"],
    )
    assert blob.objects == {}
    assert set(blob.deleted) == {saved.id, f"{saved.id}__plaintext"}


def test_the_ttl_sweep_removes_the_objects_too(scope, blob):
    """The sweep is the ONLY thing that would ever reclaim these assets."""
    saved = _save(scope, blob)
    with get_connection() as conn:
        conn.execute(
            "UPDATE conversation_attachments SET created_at = now() - interval '40 days' "
            "WHERE id = %s",
            (saved.id,),
        )
        conn.commit()

    assert attach_store.purge_expired_attachments() >= 1
    assert blob.objects == {}
