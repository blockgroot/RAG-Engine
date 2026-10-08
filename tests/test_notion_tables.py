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
