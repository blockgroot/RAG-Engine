"""A table inside a Notion page is charted like any other document table.

Notion's table rows were rendered as bare "a | b" lines with no header
separator, so the table reader never saw a table and a sales table in Notion
could never be charted.
"""

from __future__ import annotations

from app.doctables.extract import find_markdown_tables, profile
from app.sources.notion import NotionAdapter


def _rt(text):
    return [{"plain_text": text, "type": "text", "text": {"content": text}}]


def _render(blocks):
    adapter = NotionAdapter.__new__(NotionAdapter)

    def children(block_id, depth, budget, sink=None):
        return [line for b in blocks[block_id]
                for line in NotionAdapter._render_block(adapter, b, depth, budget, sink)]

    adapter._render_children_lines = children
    return "\n".join(children("page", 0, [100_000]))


def test_a_notion_table_is_read_as_a_table_with_typed_columns():
    rows = [["Region", "Revenue"], ["North", "1,20,000"], ["South", "90,000"],
            ["West | HQ", "60,000"]]
    text = _render({
        "page": [
            {"id": "h", "type": "heading_2", "heading_2": {"rich_text": _rt("Sales by region")}},
            {"id": "t", "type": "table", "has_children": True,
             "table": {"table_width": 2, "has_column_header": True}},
            {"id": "p", "type": "paragraph", "paragraph": {"rich_text": _rt("Next quarter looks good.")}},
        ],
        "t": [{"id": f"r{i}", "type": "table_row",
               "table_row": {"cells": [_rt(c) for c in row]}} for i, row in enumerate(rows)],
    })
    [raw] = find_markdown_tables(text, title="Plan")
    table = profile(raw)
    assert table.name == "Sales by region"
    assert [(c.name, c.type) for c in table.columns] == [("Region", "category"),
                                                           ("Revenue", "number")]
    assert len(table.cells) == 3
    assert "Next quarter looks good." in text  # prose around it is untouched


def _prop(kind, value):
    if kind == "title":
        return {"type": "title", "title": _rt(value)}
    if kind == "select":
        return {"type": "select", "select": {"name": value}}
    return {"type": kind, kind: value}


BUDGET = [
    {"properties": {"Department": _prop("select", "Engineering"),
                    "Quarter": _prop("title", "Q1"), "Budget": _prop("number", 6300000.0)}},
    {"properties": {"Department": _prop("select", "Sales"),
                    "Quarter": _prop("title", "Q1"), "Budget": _prop("number", 5100000)}},
    {"properties": {"Department": _prop("select", "Engineering"),
                    "Quarter": _prop("title", "Q2"), "Budget": _prop("number", 8300000)}},
]


class _Client:
    """The two Notion APIs: `data_sources` (notion-client 3.x) or the older
    `databases.query`."""

    def __init__(self, new_api: bool):
        from types import SimpleNamespace

        query = lambda **kw: {"results": BUDGET, "has_more": False}  # noqa: E731
        if new_api:
            self.databases = SimpleNamespace(
                retrieve=lambda database_id: {"data_sources": [{"id": "ds-1"}]})
            self.data_sources = SimpleNamespace(query=lambda data_source_id, **kw: query())
        else:
            self.databases = SimpleNamespace(query=lambda database_id, **kw: query())


def test_an_inline_database_is_read_as_a_table_on_either_notion_api():
    """"/table" in Notion makes an inline DATABASE, whose figures live in row
    properties: the page read as empty and could not be charted or answered."""
    import pytest

    for new_api in (True, False):
        adapter = NotionAdapter.__new__(NotionAdapter)
        adapter._client = _Client(new_api)
        lines = NotionAdapter._render_block(
            adapter, {"id": "db-1", "type": "child_database",
                      "child_database": {"title": "Quarterly budget"}}, 0, [100_000])
        [raw] = find_markdown_tables("\n".join(lines), title="Page")
        table = profile(raw)
        assert table.name == "Quarterly budget"
        assert [(c.name, c.type) for c in table.columns] == [
            ("Quarter", "category"), ("Department", "category"), ("Budget", "number")]
        assert [row["c2"] for row in table.cells] == pytest.approx([6300000, 5100000, 8300000])


def test_an_unreadable_database_costs_only_that_table():
    adapter = NotionAdapter.__new__(NotionAdapter)

    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("no access")

    adapter._client = Broken()
    assert NotionAdapter._render_block(
        adapter, {"id": "db-1", "type": "child_database",
                  "child_database": {"title": "x"}}, 0, [100_000]) == []
