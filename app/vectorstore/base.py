"""The vector store contract the rest of the app depends on.

This is the ONLY surface the application uses to persist and retrieve document
chunks. It never touches Postgres or pgvector specifics directly — those live in
concrete implementations (see ``pgvector_store.py``), so the backing store can be
swapped without changing callers.

Multi-tenant isolation is baked into the contract: every method requires an
``org_id``. There is no way to insert or query without naming the tenant.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RetrievedChunk:
    """A single search hit returned from the store."""

    content: str
    score: float  # cosine similarity in [0, 1]; higher is more similar
    document_id: str
    chunk_index: int
    org_id: str
    # Human title from ``documents.title`` when the store JOINed it (preferred
    # for citation UI). Optional so fakes / reuse paths stay lightweight.
    document_title: str | None = None


@dataclass(frozen=True)
class OrganizationRef:
    """A tenant, for listing/selection (e.g. the CLI org picker, Phase 9)."""

    id: str
    name: str
    document_count: int = 0


@dataclass(frozen=True)
class StoredSourceDocument:
    """Sync metadata for one ingested source page (incremental re-sync).

    ``provider`` (e.g. ``"notion"``, ``"google"``) partitions sync state so a
    sync for one provider never diffs against another provider's rows in the
    same org — see CLAUDE.md §4 / GOOGLE_INTEGRATION_PLAN.md §3.
    """

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
        """List existing tenants (newest first), for selection UIs like the CLI.

        Optional capability: the default raises ``NotImplementedError``; stores
        that support it (``PgVectorStore``) override it. Not tenant-scoped — it is
        an operator-facing listing, not a per-tenant read.
        """
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
    ) -> str:
        """Store a document and its chunk embeddings; return the ``document_id``.

        ``chunks`` and ``embeddings`` must be the same length and aligned by
        index. All rows are written under ``org_id``.

        ``workspace_id`` (Workspace-within-a-Workspace): ``None`` (default)
        stores an org-wide row, identical to every existing call site.
        Non-``None`` scopes the row to that sub-workspace — it is written
        alongside ``org_id``, never instead of it.
        """
        raise NotImplementedError

    @abstractmethod
    def query(
        self,
        org_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        workspace_id: str | None = None,
    ) -> list[RetrievedChunk]:
        """Return the ``top_k`` most similar chunks *within ``org_id`` only*.

        ``workspace_id`` (Workspace-within-a-Workspace): ``None`` (default)
        queries only org-wide chunks (rows with ``workspace_id IS NULL``) —
        every existing call site is unaffected. A non-``None`` value queries
        only that sub-workspace's chunks and NEVER also the org-wide ones —
        a sub-workspace's answers must never silently blend in the parent
        org's policy content (see CLAUDE.md's Workspace-within-a-Workspace
        plan §0.3 for the reasoning). Always paired with ``org_id`` — never
        resolved from ``workspace_id`` alone.
        """
        raise NotImplementedError

    def list_chunk_texts(self, org_id: str) -> list[str]:
        """Return raw chunk ``content`` strings for ``org_id`` (Phase 17 vocab).

        Used to build a per-tenant SymSpell dictionary for query spelling
        correction. Optional: default raises; ``PgVectorStore`` implements it.
        """
        raise NotImplementedError("this vector store does not support listing chunk texts")

    def keyword_search(
        self,
        org_id: str,
        query_text: str,
        query_embedding: list[float],
        top_k: int = 30,
        workspace_id: str | None = None,
    ) -> list[RetrievedChunk]:
        """Full-text (BM25-style) search within ``org_id``, ordered by keyword
        relevance (Phase 6 hybrid retrieval).

        Optional capability: the default raises ``NotImplementedError``; stores
        that support it (``PgVectorStore``) override it. Each returned chunk still
        carries its cosine ``score`` (computed against ``query_embedding``) so a
        keyword-only hit can flow through the same confidence gate as a vector hit.
        """
        raise NotImplementedError("this vector store does not support keyword search")

    def list_source_documents(
        self, org_id: str, provider: str, workspace_id: str | None = None
    ) -> list["StoredSourceDocument"]:
        """Return ingested source-page metadata for incremental sync.

        Scoped to ``provider`` (e.g. ``"notion"``, ``"google"``) as well as
        ``org_id`` — sync state for one provider must never be diffed against
        another provider's rows in the same org (see CLAUDE.md §4). Optional:
        default raises. Rows without ``source_external_id`` are omitted.
        """
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
        last_modified: "datetime | None" = None,
        workspace_id: str | None = None,
    ) -> str:
        """Replace any prior copy of this source page, then store the new chunks.

        Deletes existing rows for the same ``(org_id, provider, external_id)``
        and any legacy duplicates that share ``source_uri`` (within the same
        provider) but lack an external id, then inserts one fresh document.
        Optional capability.
        """
        raise NotImplementedError("this vector store does not support source document upsert")

    def acknowledge_source_document(
        self,
        org_id: str,
        *,
        provider: str,
        external_id: str,
        title: str,
        source_uri: str | None = None,
        last_modified: "datetime | None" = None,
        workspace_id: str | None = None,
    ) -> str:
        """Record a source page with no chunks (empty / index-only after fetch).

        Keeps change detection from reporting the same empty page as "new" forever.
        Optional capability.
        """
        raise NotImplementedError(
            "this vector store does not support source document acknowledge"
        )

    def delete_source_documents(
        self,
        org_id: str,
        provider: str,
        external_ids: list[str],
        workspace_id: str | None = None,
    ) -> int:
        """Delete ingested pages by source external id, scoped to ``provider``.

        Returns rows removed. ``workspace_id`` scoping matters here too: a
        workspace's personal Notion connection and the org's admin Notion
        connection can both be ``provider="notion"``, so ``provider`` alone
        cannot disambiguate their documents — ``workspace_id`` closes that gap.
        """
        raise NotImplementedError("this vector store does not support source document delete")

    def delete_all_source_documents(
        self,
        org_id: str,
        provider: str,
        workspace_id: str | None = None,
    ) -> int:
        """Delete every ingested page for ``provider`` in this org/workspace scope.

        Used when Disconnecting a Notion/Drive connection or swapping a Drive
        folder so answers cannot keep citing revoked content. Chunks cascade.
        """
        raise NotImplementedError(
            "this vector store does not support bulk source document delete"
        )
