"""Figures inside documents, kept as typed rows for charts.

Dataset ADAPTERS (``base.py``) turn a document into rows; every adapter writes
the same storage (``store.py``, tagged with its ``origin``) and one chart
pipeline reads it. ``factory.build_dataset_adapters`` decides which run:

- ``table_adapter`` -- tables the document already has (no AI, exact);
- ``text_adapter`` -- figures in sentences, read by an AI at ingest (and in
  Chart mode for a page ingest did not read), kept only when every cell is in
  the quoted sentence and each number binds to its own label.
"""

from .extract import Table, describe, find_markdown_tables, parse_csv, profile

__all__ = ["Table", "describe", "find_markdown_tables", "parse_csv", "profile"]
