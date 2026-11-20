"""Ask a friend asks for the asker's tick whenever the conversation drew on
something the recipient can't open. Unknown counts as private: a cited document
that was deleted, an uploaded file, a live read."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.askfriend.privacy import needs_approval
from app.db import get_connection
from app.memory.conversations import ConversationTurnRow
from .ask_friend_support import make_org
from .conftest import requires_db

pytestmark = requires_db


def _turn(cited=None, meta=None) -> ConversationTurnRow:
    return ConversationTurnRow(0, "q?", "a", datetime.now(timezone.utc), cited or [], None, meta)


def _doc(org: str, *, public: bool, viewers: list[str] | None = None) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO documents (org_id, title, doc_is_public, doc_viewers) "
            "VALUES (%s, 'd', %s, %s) RETURNING id",
            (org, public, viewers),
        ).fetchone()
        conn.commit()
    return {"n": 1, "document_id": str(row[0])}


@pytest.fixture
def org(org_cleanup):
    o = make_org()
    org_cleanup.append(o)
    return o


def test_public_citations_need_no_approval(org):
    assert not needs_approval(org, [_turn(cited=[_doc(org, public=True)])], "b@x.com")


def test_doc_recipient_cannot_open_needs_approval(org):
    assert needs_approval(org, [_turn(cited=[_doc(org, public=False, viewers=["a@x.com"])])], "b@x.com")


def test_doc_recipient_can_open_is_fine(org):
    assert not needs_approval(org, [_turn(cited=[_doc(org, public=False, viewers=["b@x.com"])])], "B@x.com")


def test_deleted_cited_doc_needs_approval(org):
    assert needs_approval(org, [_turn(cited=[{"n": 1, "document_id": str(uuid.uuid4())}])], "b@x.com")


def test_another_orgs_public_doc_counts_as_private(org, org_cleanup):
    other = make_org()
    org_cleanup.append(other)
    assert needs_approval(org, [_turn(cited=[_doc(other, public=True)])], "b@x.com")


def test_attachment_needs_approval(org):
    assert needs_approval(org, [_turn(meta={"source": "x", "attachments": ["f.pdf"]})], "b@x.com")


def test_live_read_needs_approval(org):
    meta = {"source": "github", "live_sources": [{"provider": "github", "fetched_at": "t"}]}
    assert needs_approval(org, [_turn(meta=meta)], "b@x.com")


def test_no_turns_no_approval(org):
    assert not needs_approval(org, [], "b@x.com")


def test_malformed_document_id_counts_as_private(org):
    assert needs_approval(org, [_turn(cited=[{"n": 1, "document_id": "not-a-uuid"}])], "b@x.com")
