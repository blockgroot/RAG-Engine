"""The external-source contract the ingestion path depends on.

Split into cheap *listing* (metadata) and explicit *fetching* (content), so a
sync fetches only what changed. Converting to plain text is the adapter's job.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DocAccess:
    """WHO may read one document, as the SOURCE reports it.

    ``is_public``: public within the indexing scope, not the internet.
    ``viewers``: ACL entries spelled as ``Viewer.acl`` reads them (lowercased
    email, ``domain:<host>``, ``group:<address>``; groups expand on the READ side).
    An entry no viewer can produce fails closed.

    ``None`` (no ``DocAccess``) is NOT public: for an ACL-capable provider the
    document is skipped.
    """

    is_public: bool
    viewers: tuple[str, ...] = ()
    #: True for a FALLBACK: sharing was unreadable, so ``viewers`` names only the
    #: connected account. Reported in ingest counters apart from a known audience.
    unreadable: bool = False

    @classmethod
    def scope_public(cls) -> "DocAccess":
        """Readable by everyone in the scope — today's behaviour, spelled out."""
        return cls(is_public=True, viewers=())

    @classmethod
    def restricted(cls, viewers: list[str]) -> "DocAccess":
        return cls(is_public=False, viewers=tuple(viewers))

    @classmethod
    def owner_only(cls, account_email: str) -> "DocAccess":
        """Sharing is unreadable: grant ONLY the account that could see the file.

        Better than skipping: that person can use it, and a colleague gets "not
        shared with you" (`restricted_match`) instead of "I don't know". Still fail
        closed: others who may have access are not granted it.
        """
        return cls(is_public=False, viewers=(account_email.strip().lower(),), unreadable=True)


@dataclass(frozen=True)
class SourceRef:
    """A lightweight pointer to one document, returned by ``list_documents``.

    Carries only metadata (no content), so listing a whole workspace is cheap.
    """

    external_id: str  # the source's own stable id (e.g. a Notion page id)
    title: str
    last_modified: datetime | None = None
    source_uri: str | None = None  # link back to the original (e.g. Notion URL)
    # Who last edited it, if the listing has it for free; never an extra call per document.
    last_editor: str | None = None
    # Who may read it, when the LISTING knows: revocation rides the listing
    # (ingestion.pipeline._plan_refs). ``None`` = not reported from a listing.
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
    # Per-document hard-filter tags, merged with run-level tags. STABLE ids, never
    # display names: a rename would silently stop matching.
    tags: list[str] | None = None
    # Who may read it (``DocAccess``). ``None`` = no sharing: SKIPPED for an ACL-capable provider.
    access: "DocAccess | None" = None
    # People, links and containers (``sources.meta``), only from payloads in hand,
    # never an extra call. ``None`` = nothing captured, not "none exist".
    meta: dict | None = None
    # Tables the adapter parsed (a Sheet or CSV). ``None`` = the pipeline looks for
    # pipe tables in ``content``.
    tables: list | None = None


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
