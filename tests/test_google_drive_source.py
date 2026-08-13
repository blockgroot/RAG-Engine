"""Phase 5 (Google Integration Plan): GoogleDriveAdapter.

No network: ``httpx.get`` is monkeypatched at the module attribute path
(``app.sources.google_drive.httpx.get``), mirroring ``tests/test_google_oauth.py``'s
and ``tests/test_auth.py``'s pattern for faking HTTP calls in this codebase.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.exceptions import ConfigurationError, SourceError
from app.sources import GoogleDriveAdapter
from app.sources.base import SourceDocument, SourceRef

FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"


class FakeResponse:
    def __init__(self, payload=None, *, text=None, status_code=200):
        self._payload = payload
        self._text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self._payload

    @property
    def text(self):
        return self._text


def _file(id, name, mime, modified="2026-01-15T10:30:00.000Z", parents=None, trashed=False):
    return {
        "id": id,
        "name": name,
        "mimeType": mime,
        "modifiedTime": modified,
        "trashed": trashed,
        "parents": parents or [],
    }


# --- constructor validation -------------------------------------------------


def test_empty_token_raises_configuration_error():
    with pytest.raises(ConfigurationError):
        GoogleDriveAdapter(token="", folder_id="folder-1")


def test_empty_folder_id_raises_configuration_error():
    with pytest.raises(ConfigurationError):
        GoogleDriveAdapter(token="tok-abc", folder_id="")


# --- list_documents: flat folder --------------------------------------------


def test_list_documents_flat_folder_skips_non_docs(monkeypatch):
    root_doc = _file("doc-1", "Handbook", DOC_MIME)
    subfolder = _file("folder-2", "Nested", FOLDER_MIME)
    shortcut = _file("shortcut-1", "A Shortcut", SHORTCUT_MIME)
    trashed_doc = _file("doc-trashed", "Old Doc", DOC_MIME, trashed=True)

    calls = []

    def fake_get(url, *, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers})
        # Simulate the real Drive API: "trashed = false" in q actually excludes
        # trashed items server-side (this is what we assert on below too).
        assert "trashed = false" in params["q"]
        if url.endswith("/files") and params["q"].startswith("'root'"):
            # The real Drive API would already exclude trashed_doc server-side
            # because of the "trashed = false" filter asserted above.
            return FakeResponse({"files": [root_doc, subfolder, shortcut]})
        if url.endswith("/files") and params["q"].startswith("'folder-2'"):
            return FakeResponse({"files": []})
        raise AssertionError(f"unexpected call: {url} {params}")

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)

    adapter = GoogleDriveAdapter(token="tok-abc", folder_id="root")
    refs = adapter.list_documents()

    assert [r.external_id for r in refs] == ["doc-1"]
    assert refs[0].title == "Handbook"
    assert refs[0].source_uri == "https://docs.google.com/document/d/doc-1/edit"
    assert refs[0].last_modified == datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    # trashed=false must be present in every q filter.
    for call in calls:
        assert "trashed = false" in call["params"]["q"]
    # Shared-drive params present.
    for call in calls:
        assert call["params"]["supportsAllDrives"] == "true"
        assert call["params"]["includeItemsFromAllDrives"] == "true"
    # The subfolder WAS recursed into (a second call for folder-2), the
    # shortcut was NOT (no call ever queries 'shortcut-1' as a parent).
    queried_parents = {c["params"]["q"].split("'")[1] for c in calls}
    assert "folder-2" in queried_parents
    assert "shortcut-1" not in queried_parents


# --- list_documents: nested subfolder recursion -----------------------------


def test_list_documents_recurses_into_nested_subfolders(monkeypatch):
    level0_folder = _file("folder-a", "Level A", FOLDER_MIME)
    level1_folder = _file("folder-b", "Level B", FOLDER_MIME)
    nested_doc = _file("doc-deep", "Deep Doc", DOC_MIME)

    def fake_get(url, *, params=None, headers=None, timeout=None):
        q = params["q"]
        if q.startswith("'root'"):
            return FakeResponse({"files": [level0_folder]})
        if q.startswith("'folder-a'"):
            return FakeResponse({"files": [level1_folder]})
        if q.startswith("'folder-b'"):
            return FakeResponse({"files": [nested_doc]})
        raise AssertionError(f"unexpected call: {params}")

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)

    adapter = GoogleDriveAdapter(token="tok-abc", folder_id="root")
    refs = adapter.list_documents()

    assert [r.external_id for r in refs] == ["doc-deep"]


# --- list_documents: pagination ---------------------------------------------


def test_list_documents_paginates(monkeypatch):
    page1_doc = _file("doc-1", "First", DOC_MIME)
    page2_doc = _file("doc-2", "Second", DOC_MIME)

    calls = []

    def fake_get(url, *, params=None, headers=None, timeout=None):
        calls.append(params)
        if "pageToken" not in params:
            return FakeResponse({"files": [page1_doc], "nextPageToken": "tok-page-2"})
        assert params["pageToken"] == "tok-page-2"
        return FakeResponse({"files": [page2_doc]})

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)

    adapter = GoogleDriveAdapter(token="tok-abc", folder_id="root")
    refs = adapter.list_documents()

    assert {r.external_id for r in refs} == {"doc-1", "doc-2"}
    assert len(calls) == 2


# --- list_documents: folder cycle --------------------------------------------


def test_list_documents_handles_folder_cycle(monkeypatch):
    folder_a = _file("folder-a", "A", FOLDER_MIME)
    folder_root_again = _file("root", "Root Again", FOLDER_MIME)  # cycle back to root
    doc_in_a = _file("doc-1", "Doc", DOC_MIME)

    call_count = {"root": 0, "folder-a": 0}

    def fake_get(url, *, params=None, headers=None, timeout=None):
        q = params["q"]
        if q.startswith("'root'"):
            call_count["root"] += 1
            return FakeResponse({"files": [folder_a]})
        if q.startswith("'folder-a'"):
            call_count["folder-a"] += 1
            # Points back at root -- a cycle.
            return FakeResponse({"files": [folder_root_again, doc_in_a]})
        raise AssertionError(f"unexpected call: {params}")

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)

    adapter = GoogleDriveAdapter(token="tok-abc", folder_id="root")
    refs = adapter.list_documents()

    # Terminates, finds the one real doc, doesn't loop or duplicate.
    assert [r.external_id for r in refs] == ["doc-1"]
    assert call_count["root"] == 1
    assert call_count["folder-a"] == 1


# --- fetch_document ----------------------------------------------------------


def test_fetch_document_success(monkeypatch):
    calls = []

    def fake_get(url, *, params=None, headers=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers})
        if url.endswith("/export"):
            assert params["mimeType"] == "text/markdown"
            return FakeResponse(text="# Handbook\n\nSome content.")
        if url.endswith("/files/doc-1"):
            return FakeResponse({"name": "Handbook", "modifiedTime": "2026-02-01T00:00:00.000Z"})
        raise AssertionError(f"unexpected call: {url}")

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)

    adapter = GoogleDriveAdapter(token="tok-secret", folder_id="root")
    doc = adapter.fetch_document("doc-1")

    assert isinstance(doc, SourceDocument)
    assert doc.external_id == "doc-1"
    assert doc.title == "Handbook"
    assert doc.content == "# Handbook\n\nSome content."
    assert doc.source_uri == "https://docs.google.com/document/d/doc-1/edit"
    assert doc.last_modified == datetime(2026, 2, 1, tzinfo=timezone.utc)

    assert all(c["headers"]["Authorization"] == "Bearer tok-secret" for c in calls)


def test_fetch_document_wraps_export_failure_as_source_error(monkeypatch):
    def fake_get(url, *, params=None, headers=None, timeout=None):
        if url.endswith("/export"):
            return FakeResponse(status_code=403)
        raise AssertionError("metadata call should not happen after export fails")

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)

    adapter = GoogleDriveAdapter(token="tok-abc", folder_id="root")
    with pytest.raises(SourceError):
        adapter.fetch_document("doc-huge")


# --- get_last_modified ---------------------------------------------------------


def test_get_last_modified_success(monkeypatch):
    def fake_get(url, *, params=None, headers=None, timeout=None):
        assert url.endswith("/files/doc-1")
        assert params["fields"] == "modifiedTime"
        return FakeResponse({"modifiedTime": "2026-03-10T12:00:00.000Z"})

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)

    adapter = GoogleDriveAdapter(token="tok-abc", folder_id="root")
    result = adapter.get_last_modified("doc-1")

    assert result == datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)


def test_get_last_modified_wraps_http_failure_as_source_error(monkeypatch):
    def fake_get(url, *, params=None, headers=None, timeout=None):
        return FakeResponse(status_code=500)

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)

    adapter = GoogleDriveAdapter(token="tok-abc", folder_id="root")
    with pytest.raises(SourceError):
        adapter.get_last_modified("doc-1")


# --- shape sanity -------------------------------------------------------------


def test_source_ref_and_document_field_shapes(monkeypatch):
    doc_file = _file("doc-1", "Policy Doc", DOC_MIME, modified="2026-05-05T05:05:05.000Z")

    def fake_get(url, *, params=None, headers=None, timeout=None):
        if url.endswith("/files"):
            return FakeResponse({"files": [doc_file]})
        if url.endswith("/export"):
            return FakeResponse(text="content")
        if url.endswith("/files/doc-1"):
            return FakeResponse({"name": "Policy Doc", "modifiedTime": "2026-05-05T05:05:05.000Z"})
        raise AssertionError(url)

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)

    adapter = GoogleDriveAdapter(token="tok-abc", folder_id="root")
    ref = adapter.list_documents()[0]
    assert isinstance(ref, SourceRef)
    assert ref.external_id == "doc-1"
    assert ref.title == "Policy Doc"
    assert ref.source_uri == "https://docs.google.com/document/d/doc-1/edit"
    assert ref.last_modified is not None

    doc = adapter.fetch_document("doc-1")
    assert isinstance(doc, SourceDocument)
    assert doc.external_id == "doc-1"
    assert doc.title == "Policy Doc"
    assert doc.content == "content"
    assert doc.source_uri == "https://docs.google.com/document/d/doc-1/edit"
    assert doc.last_modified is not None


# --- walk bounds (breadth, not just depth) ----------------------------------
#
# `_MAX_WALK_DEPTH` capped how DEEP the crawl went but nothing capped how WIDE.
# Every folder costs its own files.list call, so a broad tree issued an unbounded
# number of sequential Google requests inside one HTTP request — and the same
# walk runs on the Sources change-check. Same lesson as the Notion fetch bound:
# cap the walk itself.


def test_a_very_wide_folder_tree_bounds_the_number_of_api_calls(monkeypatch):
    """1000 subfolders must not mean 1000 sequential Drive calls."""
    from app.config.settings import GoogleSettings

    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        folder = kwargs["params"]["q"].split("'")[1]
        if folder == "root":
            return FakeResponse(
                {"files": [_file(f"sub-{i}", f"Sub {i}", FOLDER_MIME) for i in range(1000)]}
            )
        return FakeResponse({"files": [_file(f"doc-{folder}", "Doc", DOC_MIME)]})

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)
    adapter = GoogleDriveAdapter(
        token="tok",
        folder_id="root",
        settings=GoogleSettings(
            client_id=None,
            client_secret=None,
            redirect_uri=None,
            max_walk_folders=25,
        ),
    )
    refs = adapter.list_documents()
    assert calls["n"] <= 25, f"{calls['n']} Drive calls — the walk is unbounded"
    assert len(refs) <= 25


def test_the_document_count_is_bounded_too(monkeypatch):
    from app.config.settings import GoogleSettings

    def fake_get(url, **kwargs):
        return FakeResponse(
            {"files": [_file(f"doc-{i}", f"Doc {i}", DOC_MIME) for i in range(500)]}
        )

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)
    adapter = GoogleDriveAdapter(
        token="tok",
        folder_id="root",
        settings=GoogleSettings(
            client_id=None, client_secret=None, redirect_uri=None, max_documents=40
        ),
    )
    assert len(adapter.list_documents()) == 40


def test_truncation_is_logged_never_silent(monkeypatch, caplog):
    """A partially-walked folder must be distinguishable from a complete one."""
    from app.config.settings import GoogleSettings

    def fake_get(url, **kwargs):
        folder = kwargs["params"]["q"].split("'")[1]
        if folder == "root":
            return FakeResponse(
                {"files": [_file(f"sub-{i}", f"Sub {i}", FOLDER_MIME) for i in range(50)]}
            )
        return FakeResponse({"files": []})

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)
    adapter = GoogleDriveAdapter(
        token="tok",
        folder_id="root",
        settings=GoogleSettings(
            client_id=None, client_secret=None, redirect_uri=None, max_walk_folders=5
        ),
    )
    with caplog.at_level("WARNING"):
        adapter.list_documents()
    assert any("truncated" in r.getMessage().lower() for r in caplog.records)


def test_a_normal_folder_is_completely_unaffected_by_the_bounds(monkeypatch):
    """The bounds are a backstop; a real policy folder must walk in full."""
    def fake_get(url, **kwargs):
        folder = kwargs["params"]["q"].split("'")[1]
        if folder == "root":
            return FakeResponse(
                {"files": [_file("sub-1", "Sub", FOLDER_MIME), _file("d1", "A", DOC_MIME)]}
            )
        return FakeResponse({"files": [_file("d2", "B", DOC_MIME)]})

    monkeypatch.setattr("app.sources.google_drive.httpx.get", fake_get)
    refs = GoogleDriveAdapter(token="tok", folder_id="root").list_documents()
    assert {r.external_id for r in refs} == {"d1", "d2"}
