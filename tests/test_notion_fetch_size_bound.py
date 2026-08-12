"""NotionAdapter.fetch_document must not build an unbounded string.

Regression for a live incident: an ingestion job OOM-crashed a 512MB Render
instance before storing a single document, with the job's progress phase
stuck at "preparing" — i.e. inside the fetch itself, never reaching the
embedding step. ``_render_block``/``_render_children_lines`` recursed with no
depth or size limit, and every block with children fired its own paginated
API call, so a page with deep/wide nesting could build an unbounded string
(and an unbounded number of API calls) entirely in memory before
``sanitize_ingest_text``'s size check ever ran (that check is post-fetch, too
late to prevent the spike that built the oversized string).

No live network — the notion_client.Client is monkeypatched with a fake that
serves an arbitrarily wide/deep block tree from in-memory dicts.
"""

from __future__ import annotations

import pytest

from app.config.settings import NotionSettings
from app.sources.notion import NotionAdapter, _TRUNCATION_MARKER


class _FakeBlocksChildren:
    """Fake ``client.blocks.children`` — serves from an in-memory adjacency map."""

    def __init__(self, children_by_parent: dict[str, list[dict]]) -> None:
        self._children_by_parent = children_by_parent
        self.list_calls: list[str] = []

    def list(self, block_id: str, start_cursor=None):
        self.list_calls.append(block_id)
        return {
            "results": self._children_by_parent.get(block_id, []),
            "has_more": False,
            "next_cursor": None,
        }


class _FakePages:
    def retrieve(self, page_id: str):
        return {
            "id": page_id,
            "url": f"https://notion.so/{page_id}",
            "last_edited_time": None,
            "properties": {},
        }


class _FakeClient:
    def __init__(self, auth, children_by_parent: dict[str, list[dict]]) -> None:
        self.auth = auth
        self.blocks = type("Blocks", (), {})()
        self.blocks.children = _FakeBlocksChildren(children_by_parent)
        self.pages = _FakePages()


def _paragraph(block_id: str, text: str, has_children: bool = False) -> dict:
    return {
        "id": block_id,
        "type": "paragraph",
        "has_children": has_children,
        "paragraph": {"rich_text": [{"plain_text": text}]},
    }


def _make_adapter(monkeypatch, children_by_parent: dict[str, list[dict]]) -> NotionAdapter:
    import notion_client

    monkeypatch.setattr(
        notion_client,
        "Client",
        lambda auth: _FakeClient(auth, children_by_parent),
    )
    return NotionAdapter(NotionSettings(token="fake-token"))


def test_a_wide_page_is_truncated_not_fully_rendered(monkeypatch):
    """1000 sibling blocks of 1000 chars each (~1MB) against a tiny budget
    must stop well short of rendering all of them."""
    monkeypatch.setenv("INGEST_MAX_DOCUMENT_CHARS", "5000")

    children = {
        "page-1": [_paragraph(f"b{i}", "X" * 1000) for i in range(1000)]
    }
    adapter = _make_adapter(monkeypatch, children)

    doc = adapter.fetch_document("page-1")

    assert doc.content.endswith(_TRUNCATION_MARKER)
    # Budget is 5000 chars; each block is 1000 chars, so at most ~5-6 of the
    # 1000 available blocks should ever be rendered — nowhere near all 1000.
    assert doc.content.count("X" * 1000) <= 6


def test_a_deep_chain_stops_recursing_once_budget_is_exhausted(monkeypatch):
    """A long chain of nested blocks (each with one large-text child) must not
    keep firing paginated API calls forever once the budget is spent — this
    is what actually bounds the unbounded API fan-out, not just string length.
    """
    monkeypatch.setenv("INGEST_MAX_DOCUMENT_CHARS", "3000")

    depth = 10_000
    children: dict[str, list[dict]] = {}
    for i in range(depth):
        parent = "page-1" if i == 0 else f"b{i - 1}"
        children[parent] = [_paragraph(f"b{i}", "Y" * 500, has_children=True)]
    # last block has no children recorded -> naturally terminates if ever reached
    children.setdefault(f"b{depth - 1}", [])

    adapter = _make_adapter(monkeypatch, children)
    fake_children_resource = adapter._client.blocks.children

    doc = adapter.fetch_document("page-1")

    assert doc.content.endswith(_TRUNCATION_MARKER)
    # Budget 3000 / 500 chars per block -> at most ~6-7 levels should ever be
    # fetched, nowhere near the full chain of 10,000 recursive API calls a
    # depth-unlimited walk would make.
    assert len(fake_children_resource.list_calls) < 20


def test_a_normal_small_page_is_never_truncated(monkeypatch):
    """The common case (a real policy page) must be completely unaffected."""
    monkeypatch.setenv("INGEST_MAX_DOCUMENT_CHARS", "2000000")

    children = {
        "page-1": [
            _paragraph("b0", "Part-time employees accrue leave at half rate."),
            _paragraph("b1", "Full-time employees accrue 1.5 days per month."),
        ]
    }
    adapter = _make_adapter(monkeypatch, children)

    doc = adapter.fetch_document("page-1")

    assert _TRUNCATION_MARKER not in doc.content
    assert "Part-time employees" in doc.content
    assert "Full-time employees" in doc.content


def test_a_block_with_huge_own_text_and_no_children_is_still_bounded(monkeypatch):
    """A single block's OWN text (not its children) exceeding the budget must
    also be caught — the budget check must not only fire on recursion."""
    monkeypatch.setenv("INGEST_MAX_DOCUMENT_CHARS", "100")

    children = {
        "page-1": [
            _paragraph("b0", "A" * 200),
            _paragraph("b1", "this block should never be reached"),
        ]
    }
    adapter = _make_adapter(monkeypatch, children)

    doc = adapter.fetch_document("page-1")

    assert "this block should never be reached" not in doc.content
    assert doc.content.endswith(_TRUNCATION_MARKER)
