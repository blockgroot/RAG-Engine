"""The dataset adapter contract: one way to turn a document into chartable rows.

Every adapter writes the same storage (``doc_tables``/``doc_table_rows``,
tagged with its ``origin``); one chart pipeline reads it.

- ``TableAdapter`` (``origin="table"``): tables the document already has; no
  AI, exact.
- ``TextAdapter`` (``origin="text"``): figures in sentences, read by an AI
  inline at ingest (and in Chart mode), kept only when quote and cells check.

``wants`` is a cheap code check run on every document; ``extract`` does the work.
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
