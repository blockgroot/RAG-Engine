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
class DocAccess:
    """WHO may read one document, as the SOURCE itself reports it.

    Document-level access filtering: the smallest private unit used to be a
    SPACE, because we sync with one admin's token and stored no per-document
    ACL — so a Drive file shared with two people became readable by everyone
    in the scope that indexed it. This is the adapter's answer to "and who was
    it actually shared with?".

    ``is_public`` means public *within the scope that indexed it* — a Drive
    "anyone with the link" file, or any provider that cannot report sharing at
    all. It is NOT a claim that the document is public to the internet.

    ``viewers`` are ACL ENTRIES, and their spelling is a contract with
    ``vectorstore.base.Viewer.acl``: a lowercased email, ``domain:<host>``, or
    ``group:<address>``. A group is stored as the GROUP and expanded on the
    READ side from the asker's own memberships (``sources.google_groups``), so
    the entry survives a change in who is in that group. An entry no ``Viewer``
    can currently produce — a ``group:`` one while group expansion is switched
    off — fails CLOSED: the document is withheld rather than shown to the
    wrong person.

    ``None`` — the absence of a ``DocAccess`` — is NOT "public". It means the
    adapter could not determine sharing, and for an ACL-capable provider the
    ingestion pipeline skips that document rather than indexing it readable.
    """

    is_public: bool
    viewers: tuple[str, ...] = ()

    @classmethod
    def scope_public(cls) -> "DocAccess":
        """Readable by everyone in the scope — today's behaviour, spelled out."""
        return cls(is_public=True, viewers=())

    @classmethod
    def restricted(cls, viewers: list[str]) -> "DocAccess":
        return cls(is_public=False, viewers=tuple(viewers))


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
    # Who may read it, when the LISTING already knows. Drive's per-file
    # `permissions` ride in the `files.list` we already make, which is what
    # makes revocation free: an unshared file is re-stamped on the next sync
    # even though its content never changed and is never re-fetched
    # (see ingestion.pipeline._plan_refs). ``None`` = this adapter does not
    # report sharing from a listing.
    access: "DocAccess | None" = None


@dataclass(frozen=True)
class SourceDocument:
    """One fetched document, already converted to clean plain text."""

    external_id: str
    title: str
    content: str
    source_uri: str | None = None
    last_modified: datetime | None = None
    last_editor: str | None = None
    # Per-document hard-filter labels, merged with any run-level tags by the
    # ingestion pipeline. The adapter owns these because only it knows what
    # partitions its own source: a run-level tag list is one value for the
    # whole sync, which cannot express "this thread is in #engineering and
    # that one is in #random". Use STABLE identifiers, never display names --
    # a Slack channel rename moves no message id, so a name-based tag would
    # silently stop matching (see slack_utils.refresh_channel_names).
    tags: list[str] | None = None
    # Who may read this document (see ``DocAccess``). ``None`` means the
    # adapter reports no sharing information — which for an ACL-capable
    # provider means the document is SKIPPED, never indexed readable.
    access: "DocAccess | None" = None


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
