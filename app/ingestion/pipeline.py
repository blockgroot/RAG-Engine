"""Ingestion orchestrator: pull from a source, store into the vector store.

Wires a ``SourceAdapter`` into preprocess → chunk → [contextualize] → embed →
store. Supports *incremental* sync: list metadata cheaply, compare
``last_modified`` to stored rows, and only fetch/embed pages that are new or
changed (and drop pages removed upstream). Re-sync therefore updates rows
instead of appending duplicates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ..config.settings import ChunkingSettings, ContextualSettings
from ..embeddings import build_embedding_provider
from ..embeddings.base import EmbeddingProvider
from ..ingestion.chunking import chunk_text
from ..ingestion.sanitize import sanitize_ingest_text
from ..ingestion.contextualize import contextualize_chunks
from ..ingestion.preprocessing import preprocess
from ..llm import build_aux_llm_provider, build_llm_provider
from ..llm.base import LLMProvider
from ..sources.base import SourceAdapter, SourceRef
from ..vectorstore import build_vector_store
from ..vectorstore.base import VectorStore


# (phase, processed, total) -> None. Reported as each document finishes so a
# caller (the job worker) can persist live progress; the pipeline itself stays
# storage-agnostic and never imports app/jobs.
ProgressCallback = Callable[[str, int, int], None]

logger = logging.getLogger(__name__)


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
    # External ids written this run — used by deferred contextual enrich.
    ingested_external_ids: list[str] = field(default_factory=list)


def detect_source_changes(
    adapter: SourceAdapter,
    org_id: str,
    *,
    provider: str,
    store: VectorStore | None = None,
    workspace_id: str | None = None,
) -> ChangeReport:
    """Compare remote page metadata to stored rows — no content download.

    Cheap enough to call when an admin opens Sources: only ``list_documents``.

    ``provider`` (e.g. ``"notion"``, ``"google"``) must be supplied by the
    caller from the connection's known provider, never inferred — sync state
    is partitioned per provider so a Google sync never diffs against Notion's
    rows in the same org (see CLAUDE.md §4). ``workspace_id`` (Workspace-
    within-a-Workspace): ``None`` (default) diffs the org-wide documents,
    unchanged; a sub-workspace's documents are diffed independently.
    """
    store = store or build_vector_store()
    refs = adapter.list_documents()
    stored = {
        d.external_id: d
        for d in store.list_source_documents(org_id, provider, workspace_id=workspace_id)
    }

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
    provider: str,
    embedder: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
    chunking: ChunkingSettings | None = None,
    llm: LLMProvider | None = None,
    contextual: ContextualSettings | None = None,
    incremental: bool = True,
    workspace_id: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> IngestResult:
    """Ingest documents from ``adapter`` into ``org_id``.

    ``provider`` (e.g. ``"notion"``, ``"google"``) must be supplied by the
    caller from the connection's known provider, never inferred — sync state
    is partitioned per provider so a Google sync never diffs against Notion's
    rows in the same org (see CLAUDE.md §4). ``workspace_id`` (Workspace-
    within-a-Workspace): ``None`` (default) ingests into the org-wide space,
    unchanged from every prior caller; a non-``None`` value scopes the
    fetched documents+chunks to that sub-workspace only, and sync state
    (new/updated/removed) is diffed independently per workspace — mirroring
    exactly how ``provider`` already partitions sync state so Google and
    Notion never diff against each other's rows.

    With ``incremental=True`` (default): only new/changed pages are fetched and
    upserted; unchanged pages are skipped; pages gone from the source are
    deleted. With ``incremental=False``: every remote page is re-fetched and
    upserted (still no duplicate rows — upsert replaces by external id).
    """
    embedder = embedder or build_embedding_provider()
    store = store or build_vector_store()
    contextual = contextual or ContextualSettings.from_env()
    # Inline contextualize only when enabled AND not deferred. Deferred mode
    # embeds raw chunks here so sync can finish; enrich runs after success.
    apply_contextual_inline = contextual.enabled and not contextual.defer
    if apply_contextual_inline and llm is None:
        llm = build_aux_llm_provider()

    def report(phase: str, processed: int, total: int) -> None:
        """Surface progress without letting observability break the run."""
        if on_progress is None:
            return
        try:
            on_progress(phase, processed, total)
        except Exception:  # noqa: BLE001 - a progress sink must never fail ingestion
            pass

    report("listing", 0, 0)
    refs = adapter.list_documents()
    stored = {
        d.external_id: d
        for d in store.list_source_documents(org_id, provider, workspace_id=workspace_id)
    }

    if incremental:
        to_add, to_update, removed_ids, unchanged = _plan_refs(refs, stored)
    else:
        to_add = [r for r in refs if r.external_id not in stored]
        to_update = [r for r in refs if r.external_id in stored]
        live_ids = {r.external_id for r in refs}
        removed_ids = [eid for eid in stored if eid not in live_ids]
        unchanged = 0

    removed_n = (
        store.delete_source_documents(org_id, provider, removed_ids, workspace_id=workspace_id)
        if removed_ids
        else 0
    )

    chunks_total = 0
    skipped = 0
    doc_ids: list[str] = []
    ingested_external_ids: list[str] = []
    added_n = 0
    updated_n = 0

    work = [(r, False) for r in to_add] + [(r, True) for r in to_update]
    total_work = len(work)
    # Report before the first page so the UI is not stuck on "listing" while
    # we fetch/contextualize/embed page 1 (that used to look like a hang at 0/N).
    report("preparing", 0, total_work)

    for done, (ref, is_update) in enumerate(work, start=1):
        # processed stays at done-1 until this page is fully stored — but phase
        # advances so pollers can see movement inside a long document.
        report("preparing", done - 1, total_work)
        doc = adapter.fetch_document(ref.external_id)
        clean = preprocess(sanitize_ingest_text(doc.content))
        chunks = chunk_text(clean, chunking)
        if not chunks:
            # Remember empty/index pages so change-check does not re-flag them as new.
            store.acknowledge_source_document(
                org_id,
                provider=provider,
                external_id=doc.external_id,
                title=doc.title,
                source_uri=doc.source_uri,
                last_modified=doc.last_modified or ref.last_modified,
                workspace_id=workspace_id,
            )
            skipped += 1
            report("indexing", done, total_work)
            continue

        if apply_contextual_inline and llm is not None:
            if len(chunks) > contextual.max_chunks:
                logger.warning(
                    "Skipping contextual enrichment for %s (%s chunks > max_chunks=%s); "
                    "storing plain chunks instead",
                    ref.external_id,
                    len(chunks),
                    contextual.max_chunks,
                )
            else:
                report("contextualizing", done - 1, total_work)
                chunks = contextualize_chunks(
                    llm, clean, chunks, org_id=org_id, concurrency=contextual.concurrency
                )

        report("embedding", done - 1, total_work)
        embeddings = embedder.embed(chunks)
        document_id = store.upsert_source_document(
            org_id,
            provider=provider,
            external_id=doc.external_id,
            title=doc.title,
            chunks=chunks,
            embeddings=embeddings,
            source_uri=doc.source_uri,
            last_modified=doc.last_modified or ref.last_modified,
            workspace_id=workspace_id,
        )
        doc_ids.append(document_id)
        ingested_external_ids.append(doc.external_id)
        chunks_total += len(chunks)
        if is_update:
            updated_n += 1
        else:
            added_n += 1
        report("indexing", done, total_work)

    return IngestResult(
        documents_ingested=added_n + updated_n,
        documents_added=added_n,
        documents_updated=updated_n,
        documents_removed=removed_n,
        documents_unchanged=unchanged,
        chunks_stored=chunks_total,
        documents_skipped=skipped,
        document_ids=doc_ids,
        ingested_external_ids=ingested_external_ids,
    )


def enrich_source_contextual(
    adapter: SourceAdapter,
    org_id: str,
    *,
    provider: str,
    external_ids: list[str],
    embedder: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
    chunking: ChunkingSettings | None = None,
    llm: LLMProvider | None = None,
    contextual: ContextualSettings | None = None,
    workspace_id: str | None = None,
    on_progress: ProgressCallback | None = None,
) -> int:
    """Re-apply contextual retrieval to pages already stored by a fast sync.

    Best-effort: failures on one page skip that page and continue. Returns how
    many pages were successfully re-embedded with context prefixes. Does not
    change sync bookkeeping (last_modified already set by the fast pass).
    """
    if not external_ids:
        return 0
    contextual = contextual or ContextualSettings.from_env()
    if not contextual.enabled:
        return 0
    embedder = embedder or build_embedding_provider()
    store = store or build_vector_store()
    llm = llm or build_aux_llm_provider()

    def report(phase: str, processed: int, total: int) -> None:
        if on_progress is None:
            return
        try:
            on_progress(phase, processed, total)
        except Exception:  # noqa: BLE001
            pass

    total = len(external_ids)
    enriched = 0
    report("enriching", 0, total)
    for i, external_id in enumerate(external_ids, start=1):
        try:
            doc = adapter.fetch_document(external_id)
            clean = preprocess(sanitize_ingest_text(doc.content))
            chunks = chunk_text(clean, chunking)
            if not chunks:
                report("enriching", i, total)
                continue
            if len(chunks) > contextual.max_chunks:
                logger.warning(
                    "Skipping contextual enrichment for %s (%s chunks > max_chunks=%s); "
                    "leaving its plain chunks as-is",
                    external_id,
                    len(chunks),
                    contextual.max_chunks,
                )
                report("enriching", i, total)
                continue
            chunks = contextualize_chunks(
                llm, clean, chunks, org_id=org_id, concurrency=contextual.concurrency
            )
            embeddings = embedder.embed(chunks)
            store.upsert_source_document(
                org_id,
                provider=provider,
                external_id=doc.external_id,
                title=doc.title,
                chunks=chunks,
                embeddings=embeddings,
                source_uri=doc.source_uri,
                last_modified=doc.last_modified,
                workspace_id=workspace_id,
            )
            enriched += 1
        except Exception:  # noqa: BLE001 - one bad page must not abort enrich
            pass
        report("enriching", i, total)
    return enriched
