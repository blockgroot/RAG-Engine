"""The vector store contract the rest of the app depends on.

The only surface for persisting and retrieving chunks; Postgres specifics
live in ``pgvector_store.py``. Every method requires an ``org_id``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DateRange:
    """Hard filter on ``documents.source_last_modified``, in the ``WHERE``
    before ranking. Either bound may be ``None``. Undated documents never
    match a bounded range."""

    after: datetime | None = None
    before: datetime | None = None


@dataclass(frozen=True)
class Viewer:
    """WHO is asking, for document-level access filtering.

    ``email`` is matched against ``documents.doc_viewers`` (lowercased), the
    source's own sharing list. ``email=None`` is UNRESTRICTED (ingest, eval,
    CLI), never a member's question; ``public_only_viewer()`` is the other
    "no email". Matched by email, not user id, so a share to someone not yet
    signed up works on their first login. Build a real person's viewer with
    ``sources.google_groups.viewer_for_person`` so groups are never dropped.
    """

    email: str | None = None
    #: Google Group addresses (lowercased, no ``group:`` prefix; ``acl`` adds it).
    #: Expanded on the READ side, so a membership change needs no re-stamp. Empty
    #: (fail closed) withholds a group-shared document.
    groups: tuple[str, ...] = ()
    #: Slack channel ids whose documents this viewer may read: a ROOM, not a
    #: person. A reply carries exactly the one channel it is posted in, whose
    #: members can already read it.
    channels: tuple[str, ...] = ()
    #: Prior verified sign-in addresses (`auth.email_change`), so old grants keep
    #: matching. Ignored with no ``email``.
    aliases: tuple[str, ...] = ()
    #: Scope-public documents only. Distinct from ``Viewer(email=None)``, which is
    #: no filter at all. A Slack channel reply and an unresolvable identity get this.
    public_only: bool = False

    @classmethod
    def unrestricted(cls) -> "Viewer":
        """No document-level filter. Spelled out so a call site is greppable."""
        return cls(email=None)

    @classmethod
    def public_only_viewer(cls) -> "Viewer":
        """Scope-public documents only — nobody's private documents, ever."""
        return cls(email=None, public_only=True)

    @property
    def is_unrestricted(self) -> bool:
        return not self.public_only and not (self.email or "").strip()

    def acl(self) -> list[str]:
        """The ACL entries this person satisfies, formatted as stored.

        Write path (``sources``) and this read path MUST agree on spelling.
        Lowercased, plus ``domain:<host>`` for Drive's domain-wide shares.

        ponytail: the domain entry is INFERRED from the address, not read from
        a directory. Onyx populates its domain group from the real Workspace
        roster for exactly this reason; upgrade when we have the Admin SDK.
        """
        entries: list[str] = []
        email = (self.email or "").strip().lower()
        if not email:
            # An empty ACL matches nothing (`doc_viewers && '{}'` is false), so the
            # predicate reduces to `doc_is_public`. Groups need an identity, so they are
            # ignored here; a channel names the reading ROOM and is still honoured.
            # Lowercased to match `normalize_viewers` on write.
            return [f"channel:{c.strip().lower()}" for c in self.channels if c.strip()]
        seen: set[str] = set()
        for address in (email, *(a.strip().lower() for a in self.aliases)):
            if not address:
                continue
            for entry in (address, f"domain:{address.split('@', 1)[1]}" if "@" in address else ""):
                if entry and entry not in seen:
                    seen.add(entry)
                    entries.append(entry)
        # Spelled as `google_drive._file_access` writes it; deduplicated.
        for group in self.groups:
            entry = f"group:{group.strip().lower()}"
            if group.strip() and entry not in seen:
                seen.add(entry)
                entries.append(entry)
        # Spelled to match `sources.slack._channel_entry`, the only writer.
        for channel in self.channels:
            entry = f"channel:{channel.strip().lower()}"
            if channel.strip() and entry not in seen:
                seen.add(entry)
                entries.append(entry)
        return entries


@dataclass(frozen=True)
class RestrictedMatch:
    """What a viewer filter WITHHELD from one query, for the refusal message.

    Carries no content, title or document id: naming the match would leak a
    file the asker may not open. ``source_provider`` is already public in scope.
    """

    score: float
    source_provider: str | None


@dataclass(frozen=True)
class RetrievedChunk:
    """A single search hit returned from the store."""

    content: str
    score: float  # cosine similarity in [0, 1]; higher is more similar
    document_id: str
    chunk_index: int
    org_id: str
    # ``documents.title`` when the store JOINed it. Optional for fakes and reuse.
    document_title: str | None = None
    # Slack recap: maps ``"{channel_id}:{thread_ts}"`` to a channel name for
    # titles ingested before the ``#channel:`` prefix.
    source_external_id: str | None = None
    # Provenance for the context header (who, when, which app). Optional for
    # fakes, reuse and legacy rows.
    source_provider: str | None = None
    last_editor: str | None = None
    last_modified: datetime | None = None
    # Readable by the whole scope. Defaults True (fakes, reuse, legacy rows).
    doc_is_public: bool = True
    # Prompt-injection probability from ingest (`app/guard/`); None = unscored.
    injection_score: float | None = None
    # The document's address in its own tool: what a citation opens (`rag/cite.py`).
    source_uri: str | None = None
    # The reranker's score, apart from ``score``: the 0.35 gate is calibrated
    # for cosine and must never read it (CLAUDE.md §4). Used for the optional
    # weak-passage cutoff.
    rerank_score: float | None = None


@dataclass(frozen=True)
class OrganizationRef:
    """A tenant, for listing/selection (e.g. the CLI org picker)."""

    id: str
    name: str
    document_count: int = 0


@dataclass(frozen=True)
class StoredSourceDocument:
    """Sync metadata for one ingested source page. ``provider`` partitions sync
    state so one provider never diffs against another's rows."""

    document_id: str
    provider: str
    external_id: str
    title: str
    source_uri: str | None
    last_modified: datetime | None


class VectorStore(ABC):
    """Abstract, tenant-scoped store for document chunks and their embeddings."""

    @abstractmethod
    def create_organization(self, name: str) -> str:
        """Create a tenant and return its ``org_id``."""
        raise NotImplementedError

    def list_organizations(self) -> list["OrganizationRef"]:
        """List tenants (newest first) for operator UIs like the CLI. Not
        tenant-scoped. Optional: default raises ``NotImplementedError``."""
        raise NotImplementedError("this vector store does not support listing organizations")

    @abstractmethod
    def add_document(
        self,
        org_id: str,
        title: str,
        chunks: list[str],
        embeddings: list[list[float]],
        source_uri: str | None = None,
        workspace_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Store a document and its aligned chunk embeddings; return the ``document_id``.

        ``workspace_id`` (``None`` = org-wide) is written alongside ``org_id``,
        never instead. ``tags`` are caller-supplied labels for the tag filter.
        """
        raise NotImplementedError

    @abstractmethod
    def query(
        self,
        org_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        workspace_id: str | None = None,
        source_provider: str | None = None,
        date_range: "DateRange | None" = None,
        tags: list[str] | None = None,
        viewer: "Viewer | None" = None,
        document_ids: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Return the ``top_k`` most similar chunks *within ``org_id`` only*.

        Every filter is in the ``WHERE`` before ranking, never a re-rank signal:

        - ``viewer``: ``None`` reads everything in scope; otherwise each document
          must be scope-public or name the person in ``doc_viewers``.
        - ``workspace_id``: ``None`` reads org-wide rows only; a value reads that
          workspace ONLY, never also org-wide. Always paired with ``org_id``.
        - ``document_ids``: restrict to these documents (graph evidence).
        - ``date_range``: filter on ``source_last_modified``.
        - ``tags``: match documents whose tags overlap ANY value.
        - ``source_provider``: a relevance filter pinned per agent, not an access
          boundary.
        """
        raise NotImplementedError

    def list_chunk_texts(self, org_id: str) -> list[str]:
        """Raw chunk ``content`` for ``org_id``, for the per-tenant spelling
        dictionary. Optional: default raises."""
        raise NotImplementedError("this vector store does not support listing chunk texts")

    def keyword_search(
        self,
        org_id: str,
        query_text: str,
        query_embedding: list[float],
        top_k: int = 30,
        workspace_id: str | None = None,
        source_provider: str | None = None,
        date_range: "DateRange | None" = None,
        tags: list[str] | None = None,
        viewer: "Viewer | None" = None,
    ) -> list[RetrievedChunk]:
        """Full-text (BM25-style) search within ``org_id``, by keyword relevance.

        ``date_range``, ``source_provider``, ``tags`` and ``viewer`` behave exactly
        as on ``query``: the keyword leg must not surface what the vector leg
        filters out. Each chunk still carries its cosine ``score`` so it passes the
        same gate. Optional: default raises.
        """
        raise NotImplementedError("this vector store does not support keyword search")

    def chunks_at(
        self,
        org_id: str,
        positions: list[tuple[str, int]],
        *,
        workspace_id: str | None = None,
        viewer: Viewer | None = None,
    ) -> list[RetrievedChunk]:
        """The chunks at ``(document_id, chunk_index)`` positions, for neighbour reads.

        Same ``org_id``/``workspace_id``/``viewer`` predicate as ``query``.
        ``score`` is 0.0: never ranked, must not reach the gate. Optional.
        """
        raise NotImplementedError("this vector store does not support positional reads")

    def recent_chunks(
        self,
        org_id: str,
        provider: str,
        *,
        workspace_id: str | None = None,
        limit: int = 40,
    ) -> list[RetrievedChunk]:
        """Most RECENTLY updated chunks for one provider, by time, not similarity.

        For "catch me up" requests no single chunk resembles. Scoped like
        ``query``. ``score`` is 0.0: no query vector, so nothing for the gate.
        Optional: default raises.
        """
        raise NotImplementedError("this vector store does not support recency retrieval")

    def restricted_match(
        self,
        org_id: str,
        query_embedding: list[float],
        *,
        workspace_id: str | None = None,
        source_provider: str | None = None,
        viewer: "Viewer | None" = None,
        min_score: float = 0.0,
        tags: list[str] | None = None,
    ) -> "RestrictedMatch | None":
        """Did the ``viewer`` filter WITHHOLD a chunk scoring at least ``min_score``?

        Refusal path only, so "not shared with you" can replace "I don't know".
        A SECOND query rather than fetching restricted rows and filtering in
        Python: here they never leave the database. Nothing reaches the prompt.
        Optional: default ``None`` (never claim what we did not verify).
        """
        return None

    def list_source_documents(
        self, org_id: str, provider: str, workspace_id: str | None = None
    ) -> list["StoredSourceDocument"]:
        """Ingested source-page metadata for incremental sync, scoped to
        ``provider`` and ``org_id``. Rows without an external id are omitted.
        Optional: default raises."""
        raise NotImplementedError("this vector store does not support source document listing")

    def upsert_source_document(
        self,
        org_id: str,
        *,
        provider: str,
        external_id: str,
        title: str,
        chunks: list[str],
        embeddings: list[list[float]],
        source_uri: str | None = None,
        last_modified: datetime | None = None,
        workspace_id: str | None = None,
        tags: list[str] | None = None,
        last_editor: str | None = None,
        is_public: bool = True,
        viewers: list[str] | None = None,
        source_meta: dict | None = None,
        editor_key: str | None = None,
    ) -> str:
        """Replace any prior copy of this source page, then store the new chunks.

        ``is_public``/``viewers`` are the access set the SOURCE reports
        (``True``/``None`` = whole scope). Deletes rows for the same
        ``(org_id, provider, external_id)`` and legacy id-less duplicates sharing
        ``source_uri``. Optional.
        """
        raise NotImplementedError("this vector store does not support source document upsert")

    def set_injection_scores(
        self, document_id: str, scores: dict[int, float | None], model: str
    ) -> None:
        """Store injection scores by chunk index. ``None`` stays NULL so the
        backfill retries it. Optional."""
        raise NotImplementedError("this vector store does not store injection scores")

    def list_unscored_chunks(self, model: str, limit: int) -> list[tuple[str, int, str]]:
        """``(document_id, chunk_index, content)`` never scored by ``model``, random order.

        Cross-tenant on purpose: the backfill writes a score back and returns
        nothing to anyone.
        """
        raise NotImplementedError("this vector store does not store injection scores")

    def acknowledge_source_document(
        self,
        org_id: str,
        *,
        provider: str,
        external_id: str,
        title: str,
        source_uri: str | None = None,
        last_modified: datetime | None = None,
        workspace_id: str | None = None,
        tags: list[str] | None = None,
        last_editor: str | None = None,
        is_public: bool = True,
        viewers: list[str] | None = None,
        source_meta: dict | None = None,
        editor_key: str | None = None,
    ) -> str:
        """Record a source page with no chunks, so change detection stops calling
        it new. Optional."""
        raise NotImplementedError(
            "this vector store does not support source document acknowledge"
        )

    def replace_source_document_chunks(
        self,
        org_id: str,
        *,
        provider: str,
        external_id: str,
        chunks: list[str],
        embeddings: list[list[float]],
        workspace_id: str | None = None,
    ) -> str | None:
        """Swap a stored document's chunks, leaving its ``documents`` row untouched.

        For deferred enrichment (chunk text only): the access set and metadata were
        decided at ingest and a re-fetch cannot reproduce them (Slack reports
        sharing only on the listing). ``None`` when the row is gone.
        """
        raise NotImplementedError(
            "this vector store does not support replacing source document chunks"
        )

    def set_source_document_access(
        self,
        org_id: str,
        *,
        provider: str,
        entries: list[tuple[str, bool, list[str] | None]],
        workspace_id: str | None = None,
    ) -> int:
        """Re-stamp the access set of already-stored documents. Returns rows touched.

        This is what makes REVOCATION work: a permission change moves no content,
        so the document is "unchanged" and never re-fetched. Each
        ``(external_id, is_public, viewers)`` REPLACES the stored set, never
        unions. Optional: default is a no-op.
        """
        return 0

    def list_source_documents_missing_meta(
        self,
        org_id: str,
        *,
        provider: str,
        workspace_id: str | None = None,
        limit: int = 25,
    ) -> list[str]:
        """Stored documents whose ``source_meta`` was never captured, newest first,
        for the bounded metadata-only refresh. Optional: default is none."""
        return []

    def set_source_document_meta(
        self,
        org_id: str,
        *,
        provider: str,
        entries: list[tuple[str, dict | None, str | None]],
        workspace_id: str | None = None,
    ) -> int:
        """Store ``(external_id, meta, editor_key)`` WITHOUT re-chunking or
        re-embedding. Optional: default is a no-op."""
        return 0

    def delete_source_documents(
        self,
        org_id: str,
        provider: str,
        external_ids: list[str],
        workspace_id: str | None = None,
    ) -> int:
        """Delete ingested pages by external id, scoped to ``provider`` and
        ``workspace_id`` (a workspace's and the org's Notion are both ``notion``).
        Returns rows removed."""
        raise NotImplementedError("this vector store does not support source document delete")

    def delete_all_source_documents(
        self,
        org_id: str,
        provider: str,
        workspace_id: str | None = None,
    ) -> int:
        """Delete every page for ``provider`` in this scope (disconnect or folder
        swap), so revoked content is not cited. Chunks cascade."""
        raise NotImplementedError(
            "this vector store does not support bulk source document delete"
        )
