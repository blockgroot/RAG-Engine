"""Incremental sync: change detection upserts only new/changed pages."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.ingestion.pipeline import ChangeReport, _plan_refs, detect_source_changes, ingest_source
from app.sources.base import SourceDocument, SourceRef
from app.vectorstore.base import StoredSourceDocument

from .conftest import requires_db


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_plan_refs_splits_new_updated_unchanged_removed():
    refs = [
        SourceRef("a", "A", last_modified=_dt("2026-01-02T00:00:00")),
        SourceRef("b", "B", last_modified=_dt("2026-01-05T00:00:00")),
        SourceRef("c", "C", last_modified=_dt("2026-01-01T00:00:00")),
    ]
    stored = {
        "b": StoredSourceDocument("1", "notion", "b", "B", None, _dt("2026-01-01T00:00:00")),
        "c": StoredSourceDocument("2", "notion", "c", "C", None, _dt("2026-01-01T00:00:00")),
        "d": StoredSourceDocument("3", "notion", "d", "D", None, _dt("2026-01-01T00:00:00")),
    }
    to_add, to_update, removed, unchanged = _plan_refs(refs, stored)
    assert [r.external_id for r in to_add] == ["a"]
    assert [r.external_id for r in to_update] == ["b"]
    assert removed == ["d"]
    assert unchanged == 1


def test_detect_source_changes_metadata_only():
    class FakeAdapter:
        def list_documents(self):
            return [
                SourceRef("x", "X", last_modified=_dt("2026-02-01T00:00:00")),
                SourceRef("y", "Y", last_modified=_dt("2026-02-01T00:00:00")),
            ]

        def fetch_document(self, external_id: str):
            raise AssertionError("detect must not fetch content")

        def get_last_modified(self, external_id: str):
            raise AssertionError("detect must not fetch per-page")

    class FakeStore:
        def list_source_documents(self, org_id: str, provider: str):
            assert provider == "notion"
            return [
                StoredSourceDocument("1", "notion", "x", "X", None, _dt("2026-01-01T00:00:00")),
            ]

    report = detect_source_changes(FakeAdapter(), "org", provider="notion", store=FakeStore())
    assert isinstance(report, ChangeReport)
    assert report.new_count == 1
    assert report.updated_count == 1
    assert report.removed_count == 0
    assert report.has_changes is True


def test_detect_source_changes_partitions_by_provider():
    """A Google-scoped diff must never see another provider's rows in the same org.

    This is the regression test for the defect fixed in Phase 1 of
    GOOGLE_INTEGRATION_PLAN.md: before partitioning by provider, a Google sync
    in an org that also had Notion documents would compute
    ``removed = every_notion_page_id`` and delete them all.
    """

    class FakeAdapter:
        def list_documents(self):
            # Google's remote listing is empty (e.g. first sync, no docs yet).
            return []

        def fetch_document(self, external_id: str):
            raise AssertionError("detect must not fetch content")

        def get_last_modified(self, external_id: str):
            raise AssertionError("detect must not fetch per-page")

    class FakeStore:
        def __init__(self):
            self._rows = {
                "notion": [
                    StoredSourceDocument(
                        "1", "notion", "n1", "Notion Page", None, _dt("2026-01-01T00:00:00")
                    ),
                    StoredSourceDocument(
                        "2", "notion", "n2", "Notion Page 2", None, _dt("2026-01-01T00:00:00")
                    ),
                ],
                "google": [],
            }

        def list_source_documents(self, org_id: str, provider: str):
            return self._rows.get(provider, [])

    report = detect_source_changes(FakeAdapter(), "org", provider="google", store=FakeStore())
    assert report.removed_count == 0
    assert report.new_count == 0


def test_exclude_index_parents_drops_folder_pages():
    """Parent listed alongside its children must not count as a syncable page."""
    from app.sources.notion import _exclude_index_parents

    parent_id = "parent-1"
    child_a = "child-a"
    child_b = "child-b"
    pages = [
        {"id": child_a, "parent": {"type": "page_id", "page_id": parent_id}},
        {"id": parent_id, "parent": {"type": "workspace", "workspace": True}},
        {"id": child_b, "parent": {"type": "page_id", "page_id": parent_id}},
        {"id": "lonely", "parent": {"type": "workspace", "workspace": True}},
    ]
    kept = _exclude_index_parents(pages)
    assert {p["id"] for p in kept} == {child_a, child_b, "lonely"}


def test_exclude_index_parents_keeps_standalone_pages():
    from app.sources.notion import _exclude_index_parents

    pages = [
        {"id": "a", "parent": {"type": "workspace", "workspace": True}},
        {"id": "b", "parent": {"type": "workspace", "workspace": True}},
    ]
    assert _exclude_index_parents(pages) == pages


class _FakeAdapter:
    """Duck-typed ``SourceAdapter`` returning one fixed fake document."""

    def __init__(self, external_id: str, title: str, text: str):
        self._external_id = external_id
        self._title = title
        self._text = text

    def list_documents(self):
        return [SourceRef(self._external_id, self._title, last_modified=_dt("2026-01-01T00:00:00"))]

    def fetch_document(self, external_id: str):
        return SourceDocument(
            external_id=self._external_id,
            title=self._title,
            content=self._text,
            source_uri=f"https://example.com/{self._external_id}",
            last_modified=_dt("2026-01-01T00:00:00"),
        )

    def get_last_modified(self, external_id: str):
        return _dt("2026-01-01T00:00:00")


class _EmptyAdapter:
    """Duck-typed ``SourceAdapter`` with no remote documents at all."""

    def list_documents(self):
        return []

    def fetch_document(self, external_id: str):
        raise AssertionError("nothing to fetch")

    def get_last_modified(self, external_id: str):
        raise AssertionError("nothing to check")


@requires_db
def test_google_sync_never_deletes_notion_documents(store, embedder, org_cleanup):
    """Real Postgres round trip — the literal regression scenario from the plan.

    Ingest a Notion doc and a Google doc into the SAME org, then re-sync Google
    with an empty remote listing (as if nothing were shared yet / everything was
    removed). Before partitioning sync state by provider, this would compute
    ``removed = {the Notion doc}`` and delete it. It must not.
    """
    org_id = store.create_organization(f"Sync Isolation Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    notion_id = f"notion-{uuid.uuid4().hex[:8]}"
    google_id = f"google-{uuid.uuid4().hex[:8]}"

    notion_adapter = _FakeAdapter(notion_id, "Notion Doc", "Employees get 20 days of PTO.")
    google_adapter = _FakeAdapter(google_id, "Google Doc", "Remote work is allowed on Fridays.")

    ingest_source(
        notion_adapter,
        org_id,
        provider="notion",
        embedder=embedder,
        store=store,
        contextual=SimpleNamespace(enabled=False),
    )
    ingest_source(
        google_adapter,
        org_id,
        provider="google",
        embedder=embedder,
        store=store,
        contextual=SimpleNamespace(enabled=False),
    )

    notion_docs_before = store.list_source_documents(org_id, "notion")
    assert {d.external_id for d in notion_docs_before} == {notion_id}

    # Re-sync Google with nothing remote — must remove only Google's own doc.
    result = ingest_source(
        _EmptyAdapter(),
        org_id,
        provider="google",
        embedder=embedder,
        store=store,
        contextual=SimpleNamespace(enabled=False),
    )
    assert result.documents_removed == 1

    notion_docs_after = store.list_source_documents(org_id, "notion")
    assert {d.external_id for d in notion_docs_after} == {notion_id}

    google_docs_after = store.list_source_documents(org_id, "google")
    assert google_docs_after == []
