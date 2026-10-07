"""Figures inside documents, kept as typed rows for charts.

Dataset ADAPTERS (``base.py``) turn a document into rows; every adapter writes
the same storage (``store.py``, tagged with its ``origin``) and one chart
pipeline reads it. ``factory.build_dataset_adapters`` decides which run:

- ``table_adapter`` -- tables the document already has (no AI, exact);
- ``text_adapter`` -- figures in sentences, read by an AI in the background
  and kept only when every cell appears in the quoted sentence (opt-in).
"""

from .extract import Table, describe, find_markdown_tables, parse_csv, profile

__all__ = ["Table", "describe", "find_markdown_tables", "parse_csv", "profile"]
