"""Ingestion orchestration for source fetch, chunk, embed, and upsert."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ..config.settings import ChunkingSettings, ContextualSettings, KeywordExtractionSettings
from ..embeddings import build_embedding_provider
from ..embeddings.base import EmbeddingProvider
from ..ingestion.chunking import chunk_text
from ..ingestion.sanitize import sanitize_ingest_text
from ..ingestion.contextualize import contextualize_chunks
from ..ingestion.keywords import append_keyword_line
from ..ingestion.preprocessing import preprocess
from ..llm import build_aux_llm_provider
from ..llm.base import LLMProvider
from ..sources.base import SourceAdapter, SourceRef
from ..vectorstore import build_vector_store
from ..vectorstore.base import VectorStore


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


_MAX_REMOVAL_FRACTION = 0.5
_MIN_STORED_FOR_REMOVAL_GUARD = 5


def _sanitize_removals(removed_ids: list[str], stored_count: int) -> tuple[list[str], bool]:
    """Refuse a removal set that would wipe out most of what's on record.

    A handful of genuinely unshared/deleted pages always passes through
    untouched — this only trips once there's real scale on record AND a
    single listing call claims most of it vanished at once, which real-world
    unsharing essentially never does at that scale but a flaky API response
    can.
    """
    if not removed_ids or stored_count < _MIN_STORED_FOR_REMOVAL_GUARD:
        return removed_ids, False
    if len(removed_ids) / stored_count > _MAX_REMOVAL_FRACTION:
        return [], True
    return removed_ids, False


_EMPTY_LISTING_CONFIRM_DELAY_SECONDS = 5


def _empty_listing_is_confirmed(
    adapter: SourceAdapter, *, stored_count: int, live_count: int
) -> bool:
    """Second opinion before acting on "the source has nothing at all".

    Returns ``True`` when the wipe should proceed — including when this was
    never a total wipe in the first place, so callers can use it as a plain
    gate. A failing re-list counts as *not* confirmed: an error is not
    evidence of emptiness.
    """
    if live_count > 0 or stored_count == 0:
        return True
    time.sleep(_EMPTY_LISTING_CONFIRM_DELAY_SECONDS)
    try:
        return not adapter.list_documents()
    except Exception:  # noqa: BLE001 - a failed re-list must never authorize a wipe
        logger.warning("Re-listing to confirm an empty source failed", exc_info=True)
        return False


_FIRST_SYNC_SUSPICIOUS_PAGE_COUNT = 1
_FIRST_SYNC_RETRY_DELAYS = (5,)  # interactive change-check: stay snappy
_FIRST_SYNC_INGEST_RETRY_DELAYS = (5, 15, 30)  # background job: patience is free


def _list_documents_with_first_sync_retry(
    adapter: SourceAdapter,
    *,
    is_first_sync: bool,
    retry_delays: tuple[int, ...] = _FIRST_SYNC_RETRY_DELAYS,
) -> list[SourceRef]:
    refs = adapter.list_documents()
    for delay in retry_delays:
        if not is_first_sync or len(refs) > _FIRST_SYNC_SUSPICIOUS_PAGE_COUNT:
            break
        logger.info(
            "First sync returned only %d page(s) — retrying after %ds in "
            "case the source's search index is still catching up on a "
            "just-granted connection.",
            len(refs),
            delay,
        )
        time.sleep(delay)
        retried = adapter.list_documents()
        if len(retried) > len(refs):
            refs = retried
    return refs


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
    stored = {
        d.external_id: d
        for d in store.list_source_documents(org_id, provider, workspace_id=workspace_id)
    }
    refs = _list_documents_with_first_sync_retry(adapter, is_first_sync=not stored)

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

    removed_ids = [eid for eid in stored if eid not in live_ids]
    if stored and not refs:
        safe_removed, suspicious = [], True
    else:
        safe_removed, suspicious = _sanitize_removals(removed_ids, len(stored))
        if safe_removed and not _empty_listing_is_confirmed(
            adapter, stored_count=len(stored), live_count=len(refs)
        ):
            safe_removed, suspicious = [], True
    if suspicious:
        logger.warning(
            "detect_source_changes: %d of %d previously known documents look "
            "removed for org=%s provider=%s workspace=%s in a single listing "
            "call — treating as an unreliable/transient read, not a real mass "
            "removal. Reporting 0 removed.",
            len(removed_ids), len(stored), org_id, provider, workspace_id,
        )
    return ChangeReport(
        new_count=new_n,
        updated_count=updated_n,
        removed_count=len(safe_removed),
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


def _reindex_slack_docs_missing_channel_prefix(
    refs: list[SourceRef],
    stored: dict,
    to_update: list[SourceRef],
    unchanged: int,
) -> tuple[list[SourceRef], int]:
    """Re-fetch Slack threads whose stored title has no ``#channel:`` prefix.

    ``fetch_document`` used to save the raw message as the title. Ask chips
    name a channel, and recap/keyword search cannot confirm a match without
    that prefix (or the ``Channel: #x`` line now written into chunk text).
    One Update after this ships backfills them; Notion/Drive are untouched.
    """
    already = {r.external_id for r in to_update}
    extra: list[SourceRef] = []
    for ref in refs:
        if ref.external_id in already:
            continue
        existing = stored.get(ref.external_id)
        if existing is None:
            continue
        if (existing.title or "").lstrip().startswith("#"):
            continue
        extra.append(ref)
    if not extra:
        return to_update, unchanged
    return to_update + extra, max(0, unchanged - len(extra))


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
    keywords: KeywordExtractionSettings | None = None,
    incremental: bool = True,
    workspace_id: str | None = None,
    tags: list[str] | None = None,
    on_progress: ProgressCallback | None = None,
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
    keywords = keywords or KeywordExtractionSettings.from_env()
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
    stored = {
        d.external_id: d
        for d in store.list_source_documents(org_id, provider, workspace_id=workspace_id)
    }
    refs = _list_documents_with_first_sync_retry(
        adapter,
        is_first_sync=not stored,
        retry_delays=_FIRST_SYNC_INGEST_RETRY_DELAYS,
    )

    if incremental:
        to_add, to_update, removed_ids, unchanged = _plan_refs(refs, stored)
    else:
        to_add = [r for r in refs if r.external_id not in stored]
        to_update = [r for r in refs if r.external_id in stored]
        live_ids = {r.external_id for r in refs}
        removed_ids = [eid for eid in stored if eid not in live_ids]
        unchanged = 0

    if provider == "slack":
        to_update, unchanged = _reindex_slack_docs_missing_channel_prefix(
            refs, stored, to_update, unchanged
        )

    removed_ids, suspicious_removal = _sanitize_removals(removed_ids, len(stored))
    if removed_ids and not _empty_listing_is_confirmed(
        adapter, stored_count=len(stored), live_count=len(refs)
    ):
        removed_ids, suspicious_removal = [], True
    if suspicious_removal:
        logger.warning(
            "ingest_source: refusing to delete a suspiciously large share of "
            "previously known documents for org=%s provider=%s workspace=%s in "
            "one run — this looks like an unreliable/transient source listing "
            "(pagination race, indexing lag, rate limit) rather than a real "
            "mass unshare/delete. Skipping removal this run; re-run once the "
            "source's listing is confirmed stable if pages were genuinely "
            "removed.",
            org_id, provider, workspace_id,
        )

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
    report("preparing", 0, total_work)

    for done, (ref, is_update) in enumerate(work, start=1):
        report("preparing", done - 1, total_work)
        doc = adapter.fetch_document(ref.external_id)
        clean = preprocess(sanitize_ingest_text(doc.content))
        chunks = chunk_text(clean, chunking)
        raw_chunks = chunks
        if not chunks:
            store.acknowledge_source_document(
                org_id,
                provider=provider,
                external_id=doc.external_id,
                title=doc.title,
                source_uri=doc.source_uri,
                last_modified=doc.last_modified or ref.last_modified,
                workspace_id=workspace_id,
                tags=tags,
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
                    llm,
                    clean,
                    chunks,
                    org_id=org_id,
                    concurrency=contextual.concurrency,
                    hypothetical_questions=contextual.hypothetical_questions,
                )

        if keywords.enabled:
            chunks = [
                append_keyword_line(stored, raw, keywords.top_n)
                for stored, raw in zip(chunks, raw_chunks)
            ]

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
            tags=tags,
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
    keywords: KeywordExtractionSettings | None = None,
    workspace_id: str | None = None,
    tags: list[str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> int:
    """Re-apply deferred contextual retrieval to pages already stored."""
    if not external_ids:
        return 0
    contextual = contextual or ContextualSettings.from_env()
    if not contextual.enabled:
        return 0
    keywords = keywords or KeywordExtractionSettings.from_env()
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
            raw_chunks = chunks
            chunks = contextualize_chunks(
                llm,
                clean,
                chunks,
                org_id=org_id,
                concurrency=contextual.concurrency,
                hypothetical_questions=contextual.hypothetical_questions,
            )
            if keywords.enabled:
                chunks = [
                    append_keyword_line(stored, raw, keywords.top_n)
                    for stored, raw in zip(chunks, raw_chunks)
                ]
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
                tags=tags,
            )
            enriched += 1
        except Exception:  # noqa: BLE001 - one bad page must not abort enrich
            pass
        report("enriching", i, total)
    return enriched
