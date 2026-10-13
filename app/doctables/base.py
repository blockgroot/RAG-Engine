"""The dataset adapter contract: one way to turn a document into chartable rows.

Every adapter writes the SAME storage (``doc_tables``/``doc_table_rows``,
tagged with its ``origin``), and one chart pipeline reads it -- the AI picks a
table and columns, code checks the pick, SQL adds up the rows. Adapters differ
only in WHERE the rows come from:

- ``TableAdapter`` (``origin="table"``): a table the document already has -- a
  Sheet, a CSV, a pipe table in Notion/Docs, a Word table. No AI: the source
  already labels every number, so the rows are exact.
- ``TextAdapter`` (``origin="text"``): figures written in sentences. Only an AI
  can read prose into rows, so it runs inline at ingest on the background
  model budget, and Chart mode reads a matching page ingest did not cover.
  Code keeps a row only when its quote is in the document, every cell is in
  its quote, and each number binds to its own label and measure.

Two methods, split on purpose: ``wants`` is a cheap code check that runs at
ingest for every document (no AI, no I/O), and ``extract`` does the work.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .extract import Table


@dataclass(frozen=True)
class DocumentText:
    """What an adapter reads: the document as ingestion already has it."""

    external_id: str
    title: str
    content: str
    #: Rows the SOURCE adapter parsed itself (a Sheet or CSV), when it did.
    tables: tuple | None = None


class DatasetAdapter(ABC):
    #: Stored on every table this adapter writes; an adapter only ever
    #: replaces its OWN tables for a document.
    origin: str = ""

    @abstractmethod
    def wants(self, doc: DocumentText) -> bool:
        """Could this document hold anything for this adapter? No AI, no I/O."""

    @abstractmethod
    def extract(self, doc: DocumentText) -> list[Table]:
        """The tables found. Raises ``ProviderError`` on a failure that may be
        retried; returns ``[]`` when there is genuinely nothing."""
