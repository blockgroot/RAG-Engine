"""Figures inside documents, kept as typed rows for charts.

Adapters (``base.py``) write one storage (``store.py``, tagged by ``origin``);
``factory.build_dataset_adapters`` decides which run: ``table_adapter``
(existing tables, no AI) and ``text_adapter`` (figures in sentences, checked
against their quotes).
"""

from .extract import Table, describe, find_markdown_tables, parse_csv, profile

__all__ = ["Table", "describe", "find_markdown_tables", "parse_csv", "profile"]
