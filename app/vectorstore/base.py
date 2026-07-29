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
    ) -> str:
        """Store a document and its chunk embeddings; return the ``document_id``.

        ``chunks`` and ``embeddings`` must be the same length and aligned by
        index. All rows are written under ``org_id``.
        """
        raise NotImplementedError

    @abstractmethod
    def query(
        self,
        org_id: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        """Return the ``top_k`` most similar chunks *within ``org_id`` only*."""
        raise NotImplementedError

    def keyword_search(
        self,
        org_id: str,
        query_text: str,
        query_embedding: list[float],
        top_k: int = 30,
    ) -> list[RetrievedChunk]:
        """Full-text (BM25-style) search within ``org_id``, ordered by keyword
        relevance (Phase 6 hybrid retrieval).

        Optional capability: the default raises ``NotImplementedError``; stores
        that support it (``PgVectorStore``) override it. Each returned chunk still
        carries its cosine ``score`` (computed against ``query_embedding``) so a
        keyword-only hit can flow through the same confidence gate as a vector hit.
        """
        raise NotImplementedError("this vector store does not support keyword search")

    def list_source_documents(self, org_id: str, provider: str) -> list["StoredSourceDocument"]:
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
    ) -> str:
        """Record a source page with no chunks (empty / index-only after fetch).

        Keeps change detection from reporting the same empty page as "new" forever.
        Optional capability.
        """
        raise NotImplementedError(
            "this vector store does not support source document acknowledge"
        )

    def delete_source_documents(self, org_id: str, provider: str, external_ids: list[str]) -> int:
        """Delete ingested pages by source external id, scoped to ``provider``.

        Returns rows removed.
        """
        raise NotImplementedError("this vector store does not support source document delete")
