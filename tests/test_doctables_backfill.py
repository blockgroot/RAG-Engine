"""Tables for documents indexed before charts read tables.

Pinned: a sync never re-fetches an unchanged document, so the backfill
re-reads a bounded batch of never-checked documents per sync; each is checked
once (stamped), a fetch failure is retried on a later sync, a document the
source no longer lists is skipped, and ingestion stamps what it reads itself.
"""

from __future__ import annotations

import uuid

import pytest

from app.db import get_connection
from app.doctables import store as table_store
from app.ingestion.pipeline import _store_tables, backfill_tables
from app.sources.base import SourceDocument
from app.vectorstore.base import Viewer
from .conftest import requires_db

pytestmark = requires_db

SHEET = "Sales\n\n| Region | Revenue |\n|---|---|\n| North | 10 |\n| South | 5 |\n"
VIEWER = Viewer(email="sana@acme.test")


class FakeSource:
    def __init__(self, fail=()):
        self.fail, self.fetched = set(fail), []

    def fetch_document(self, external_id):
        self.fetched.append(external_id)
        if external_id in self.fail:
            raise RuntimeError("403")
        return SourceDocument(external_id=external_id, title="Sales", content=SHEET)


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"backfill-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _indexed(org_id):
    """A document as an older sync left it: indexed, never checked for tables."""
    ext = uuid.uuid4().hex
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO documents (org_id, title, source_uri, source_provider,
                   source_external_id) VALUES (%s, 'Sales', 'https://notion.test/p',
                   'notion', %s) RETURNING id""",
            (org_id, ext),
        ).fetchone()
        conn.commit()
    return str(row[0]), ext


def _checked(doc_id):
    with get_connection() as conn:
        return conn.execute("SELECT tables_checked_at IS NOT NULL FROM documents "
                            "WHERE id = %s", (doc_id,)).fetchone()[0]


def _run(org_id, source, **kw):
    return backfill_tables(source, org_id=org_id, provider="notion", **kw)


def test_an_old_document_gets_its_tables_once(org):
    doc_id, ext = _indexed(org)
    source = FakeSource()
    assert _run(org, source, batch=5) == 1
    [table] = table_store.list_tables(org_id=org, workspace_id=None, viewer=VIEWER)
    assert table.name == "Sales"
    assert _checked(doc_id)
    assert _run(org, source, batch=5) == 0  # never re-read
    assert source.fetched == [ext]


def test_a_batch_is_bounded(org):
    for _ in range(4):
        _indexed(org)
    assert _run(org, FakeSource(), batch=2) == 2
    assert _run(org, FakeSource(), batch=2) == 2
    assert _run(org, FakeSource(), batch=2) == 0


def test_a_fetch_failure_is_tried_again_next_sync(org):
    doc_id, ext = _indexed(org)
    assert _run(org, FakeSource(fail={ext}), batch=5) == 0
    assert not _checked(doc_id)
    assert _run(org, FakeSource(), batch=5) == 1


def test_documents_this_sync_handled_or_no_longer_listed_are_skipped(org):
    _, just_ingested = _indexed(org)
    _, gone = _indexed(org)
    source = FakeSource()
    assert _run(org, source, batch=5, skip_ids={just_ingested},
                live_ids={just_ingested}) == 0
    assert source.fetched == []


def test_off_when_the_batch_is_zero(org):
    _indexed(org)
    assert _run(org, FakeSource(), batch=0) == 0


def test_ingestion_stamps_what_it_reads(org):
    doc_id, ext = _indexed(org)
    _store_tables(doc_id, SourceDocument(external_id=ext, title="Notes",
                                         content="No figures here."),
                  org_id=org, workspace_id=None)
    assert _checked(doc_id)
    assert _run(org, FakeSource(), batch=5) == 0
