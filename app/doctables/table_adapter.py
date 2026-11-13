"""Tables the document already has, read without AI: rows a source adapter
parsed itself, else pipe tables in the text. Types from ``extract.profile``.
"""

from __future__ import annotations

from . import extract
from .base import DatasetAdapter, DocumentText

ORIGIN = "table"


class TableAdapter(DatasetAdapter):
    origin = ORIGIN

    def wants(self, doc: DocumentText) -> bool:
        # A pipe table needs a `|---|` separator line; that is the cheap test.
        return bool(doc.tables) or "|" in (doc.content or "") and "---" in doc.content

    def extract(self, doc: DocumentText) -> list[extract.Table]:
        raw = list(doc.tables) if doc.tables is not None else extract.find_markdown_tables(
            doc.content or "", title=doc.title,
        )
        tables = [t for t in (extract.profile(r) for r in raw) if t is not None]
        return tables[: extract.MAX_TABLES_PER_DOCUMENT]
