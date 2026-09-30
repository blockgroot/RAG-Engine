"""Tables found inside documents, kept as typed rows for charts.

An orchestrator over existing pieces (Postgres, the document access
predicate), so no ``base.py``/``factory.py`` -- there is no second backend to
abstract over (CLAUDE.md §2).
"""

from .extract import Table, describe, find_markdown_tables, parse_csv, profile

__all__ = ["Table", "describe", "find_markdown_tables", "parse_csv", "profile"]
