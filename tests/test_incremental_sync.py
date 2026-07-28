"""Incremental sync: change detection upserts only new/changed pages."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.ingestion.pipeline import ChangeReport, _plan_refs, detect_source_changes
from app.sources.base import SourceRef
from app.vectorstore.base import StoredSourceDocument


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_plan_refs_splits_new_updated_unchanged_removed():
    refs = [
        SourceRef("a", "A", last_modified=_dt("2026-01-02T00:00:00")),
        SourceRef("b", "B", last_modified=_dt("2026-01-05T00:00:00")),
        SourceRef("c", "C", last_modified=_dt("2026-01-01T00:00:00")),
    ]
    stored = {
        "b": StoredSourceDocument("1", "b", "B", None, _dt("2026-01-01T00:00:00")),
        "c": StoredSourceDocument("2", "c", "C", None, _dt("2026-01-01T00:00:00")),
        "d": StoredSourceDocument("3", "d", "D", None, _dt("2026-01-01T00:00:00")),
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
        def list_source_documents(self, org_id: str):
            return [
                StoredSourceDocument("1", "x", "X", None, _dt("2026-01-01T00:00:00")),
            ]

    report = detect_source_changes(FakeAdapter(), "org", store=FakeStore())
    assert isinstance(report, ChangeReport)
    assert report.new_count == 1
    assert report.updated_count == 1
    assert report.removed_count == 0
    assert report.has_changes is True
