"""Ingestion orchestrator: pull from a source, store into the vector store.

Wires a ``SourceAdapter`` into preprocess → chunk → [contextualize] → embed →
store. Supports *incremental* sync: list metadata cheaply, compare
``last_modified`` to stored rows, and only fetch/embed pages that are new or
changed (and drop pages removed upstream). Re-sync therefore updates rows
instead of appending duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config.settings import ChunkingSettings, ContextualSettings
from ..embeddings import build_embedding_provider
from ..embeddings.base import EmbeddingProvider
from ..ingestion.chunking import chunk_text
from ..ingestion.contextualize import contextualize_chunks
from ..ingestion.preprocessing import preprocess
from ..llm import build_llm_provider
from ..llm.base import LLMProvider
from ..sources.base import SourceAdapter, SourceRef
from ..vectorstore import build_vector_store
from ..vectorstore.base import VectorStore


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True)
class ChangeReport:
    """Result of a metadata-only change check (no content fetch / embed)."""

    new_count: int = 0
    updated_count: int = 0
    removed_count: int = 0
    unchanged_count: int = 0
    remote_total: int = 0

    @property
    def has_changes(self) -> bool:
        return (self.new_count + self.updated_count + self.removed_count) > 0


@dataclass(frozen=True)
class IngestResult:
    """Summary of one ingestion run for a single org."""

    documents_ingested: int = 0  # new + updated pages written this run
    documents_added: int = 0
    documents_updated: int = 0
    documents_removed: int = 0
    documents_unchanged: int = 0
    chunks_stored: int = 0
    documents_skipped: int = 0  # fetched but had no usable text
    document_ids: list[str] = field(default_factory=list)


def detect_source_changes(
    adapter: SourceAdapter,
    org_id: str,
    *,
    store: VectorStore | None = None,
) -> ChangeReport:
    """Compare remote page metadata to stored rows — no content download.

    Cheap enough to call when an admin opens Sources: only ``list_documents``.
    """
    store = store or build_vector_store()
    refs = adapter.list_documents()
    stored = {d.external_id: d for d in store.list_source_documents(org_id)}

    new_n = updated_n = unchanged_n = 0
    live_ids: set[str] = set()
    for ref in refs:
        live_ids.add(ref.external_id)
        existing = stored.get(ref.external_id)
        if existing is None:
            new_n += 1
            continue
        remote_m = _aware(ref.last_modified)
        local_m = _aware(existing.last_modified)
        if remote_m is not None and local_m is not None and remote_m > local_m:
            updated_n += 1
        elif remote_m is not None and local_m is None:
            updated_n += 1
        else:
            unchanged_n += 1

    removed_n = sum(1 for eid in stored if eid not in live_ids)
    return ChangeReport(
        new_count=new_n,
        updated_count=updated_n,
        removed_count=removed_n,
        unchanged_count=unchanged_n,
        remote_total=len(refs),
    )


def _plan_refs(
    refs: list[SourceRef],
    stored: dict,
) -> tuple[list[SourceRef], list[SourceRef], list[str], int]:
    """Split refs into new / updated / unchanged; return removed external ids."""
    to_add: list[SourceRef] = []
    to_update: list[SourceRef] = []
    unchanged = 0
    live_ids = {r.external_id for r in refs}
    for ref in refs:
        existing = stored.get(ref.external_id)
        if existing is None:
            to_add.append(ref)
            continue
        remote_m = _aware(ref.last_modified)
        local_m = _aware(existing.last_modified)
        if remote_m is not None and local_m is not None and remote_m > local_m:
            to_update.append(ref)
        elif remote_m is not None and local_m is None:
            to_update.append(ref)
        else:
            unchanged += 1
    removed = [eid for eid in stored if eid not in live_ids]
    return to_add, to_update, removed, unchanged


def ingest_source(
    adapter: SourceAdapter,
    org_id: str,
    *,
    embedder: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
    chunking: ChunkingSettings | None = None,
    llm: LLMProvider | None = None,
    contextual: ContextualSettings | None = None,
    incremental: bool = True,
) -> IngestResult:
    """Ingest documents from ``adapter`` into ``org_id``.

    With ``incremental=True`` (default): only new/changed pages are fetched and
    upserted; unchanged pages are skipped; pages gone from the source are
    deleted. With ``incremental=False``: every remote page is re-fetched and
    upserted (still no duplicate rows — upsert replaces by external id).
    """
    embedder = embedder or build_embedding_provider()
    store = store or build_vector_store()
    contextual = contextual or ContextualSettings.from_env()
    if contextual.enabled and llm is None:
        llm = build_llm_provider()

    refs = adapter.list_documents()
    stored = {d.external_id: d for d in store.list_source_documents(org_id)}

    if incremental:
        to_add, to_update, removed_ids, unchanged = _plan_refs(refs, stored)
    else:
        to_add = [r for r in refs if r.external_id not in stored]
        to_update = [r for r in refs if r.external_id in stored]
        live_ids = {r.external_id for r in refs}
        removed_ids = [eid for eid in stored if eid not in live_ids]
        unchanged = 0

    removed_n = store.delete_source_documents(org_id, removed_ids) if removed_ids else 0

    chunks_total = 0
    skipped = 0
    doc_ids: list[str] = []
    added_n = 0
    updated_n = 0

    for ref, is_update in [(r, False) for r in to_add] + [(r, True) for r in to_update]:
        doc = adapter.fetch_document(ref.external_id)
        clean = preprocess(doc.content)
        chunks = chunk_text(clean, chunking)
        if not chunks:
            skipped += 1
            continue

        if contextual.enabled and llm is not None:
            chunks = contextualize_chunks(llm, clean, chunks)

        embeddings = embedder.embed(chunks)
        document_id = store.upsert_source_document(
            org_id,
            external_id=doc.external_id,
            title=doc.title,
            chunks=chunks,
            embeddings=embeddings,
            source_uri=doc.source_uri,
            last_modified=doc.last_modified or ref.last_modified,
        )
        doc_ids.append(document_id)
        chunks_total += len(chunks)
        if is_update:
            updated_n += 1
        else:
            added_n += 1

    return IngestResult(
        documents_ingested=added_n + updated_n,
        documents_added=added_n,
        documents_updated=updated_n,
        documents_removed=removed_n,
        documents_unchanged=unchanged,
        chunks_stored=chunks_total,
        documents_skipped=skipped,
        document_ids=doc_ids,
    )
