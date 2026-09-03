"""The external-source contract the ingestion path depends on.

A *source* is any external system that holds documents we want to ingest —
Notion now; Google Drive/Docs/Sheets, GitHub, and Slack later. Each gets a
concrete adapter implementing this one interface, so the ingestion pipeline
(``app.ingestion.pipeline``) can pull content from any of them without knowing
which one it is talking to.

The interface is deliberately split into cheap *listing* (metadata only) and
explicit *fetching* (pulls the full content), which is what makes incremental
sync possible later (list refs, compare ``last_modified``, fetch only what
changed). Turning a source's native format into clean plain text is the
adapter's job — that never leaks into the ingestion pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceRef:
    """A lightweight pointer to one document, returned by ``list_documents``.

    Carries only metadata (no content), so listing a whole workspace is cheap.
    """

    external_id: str  # the source's own stable id (e.g. a Notion page id)
    title: str
    last_modified: datetime | None = None
    source_uri: str | None = None  # link back to the original (e.g. Notion URL)
    # Who last edited it, as the source names them. Optional because not every
    # source exposes one, and a listing must stay cheap -- an adapter that
    # would need an extra request per document should leave this None rather
    # than turning one listing into N calls.
    last_editor: str | None = None


@dataclass(frozen=True)
class SourceDocument:
    """One fetched document, already converted to clean plain text."""

    external_id: str
    title: str
    content: str
    source_uri: str | None = None
    last_modified: datetime | None = None
    last_editor: str | None = None


class SourceAdapter(ABC):
    """Abstract adapter over an external content source."""

    @abstractmethod
    def list_documents(self) -> list[SourceRef]:
        """Return references to every document this adapter can access.

        Implementations must raise ``core.exceptions.SourceError`` on failure.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_document(self, external_id: str) -> SourceDocument:
        """Fetch one document by id and return it as clean plain text."""
        raise NotImplementedError

    @abstractmethod
    def get_last_modified(self, external_id: str) -> datetime | None:
        """Return when the document was last edited (for incremental sync)."""
        raise NotImplementedError
