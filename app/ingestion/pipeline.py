"""Ingestion orchestration for source fetch, chunk, embed, and upsert."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from ..config.settings import (
    ChunkingSettings,
    ContextualSettings,
    GraphSettings,
    GuardSettings,
    KeywordExtractionSettings,
)
from ..embeddings import build_embedding_provider
from ..embeddings.base import EmbeddingProvider
from ..guard import InjectionGuard, build_injection_guard
from ..ingestion.chunking import chunk_text
from ..ingestion.sanitize import sanitize_ingest_text
from ..ingestion.contextualize import contextualize_chunks
from ..ingestion.keywords import append_keyword_line
from ..ingestion.preprocessing import preprocess
from ..llm import build_aux_llm_provider
from ..llm.base import LLMProvider
from ..sources.base import SourceAdapter, SourceRef
from ..sources.factory import ACL_CAPABLE
from ..sources.meta import editor_key, extract_links, merge_meta
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
    # Files whose sharing the source would not report: a new one is left out,
    # an indexed one keeps its viewers. Same fix for both, so one count,
    # shown to the admin.
    documents_permission_unreadable: int = 0
    document_ids: list[str] = field(default_factory=list)
    # External ids written this run — used by deferred contextual enrich.
    ingested_external_ids: list[str] = field(default_factory=list)
    # Indexed documents whose `source_meta` the metadata refresh captured.
    meta_refreshed_external_ids: list[str] = field(default_factory=list)


_MAX_REMOVAL_FRACTION = 0.5
_MIN_STORED_FOR_REMOVAL_GUARD = 5


def _sanitize_removals(removed_ids: list[str], stored_count: int) -> tuple[list[str], bool]:
    """Refuse a removal set that would wipe out most of what's on record:
    real unsharing never does that at scale, a flaky listing can.
    """
    if not removed_ids or stored_count < _MIN_STORED_FOR_REMOVAL_GUARD:
        return removed_ids, False
    if len(removed_ids) / stored_count > _MAX_REMOVAL_FRACTION:
        return [], True
    return removed_ids, False


def _removable(adapter: SourceAdapter, removed_ids: list[str], *, provider: str) -> list[str]:
    """Drop removals the listing cannot vouch for.

    Absence proves deletion only if the listing was complete
    (``listing_complete``) and would have included the document
    (``may_remove``: an old Slack thread outside the window is not deleted).
    """
    if not removed_ids:
        return removed_ids
    if getattr(adapter, "listing_complete", True) is False:
        logger.warning(
            "ingest: %s listing was incomplete -- deleting none of the %d unlisted documents",
            provider, len(removed_ids),
        )
        return []
    may_remove = getattr(adapter, "may_remove", None)
    if may_remove is None:
        return removed_ids
    kept = [eid for eid in removed_ids if may_remove(eid)]
    if len(kept) < len(removed_ids):
        logger.info(
            "ingest: %s kept %d unlisted documents outside the listing's window",
            provider, len(removed_ids) - len(kept),
        )
    return kept


_EMPTY_LISTING_CONFIRM_DELAY_SECONDS = 5


def _empty_listing_is_confirmed(
    adapter: SourceAdapter, *, stored_count: int, live_count: int
) -> bool:
    """Second opinion before acting on "the source has nothing at all".

    ``True`` lets the removal proceed (also when it is not a total wipe). A
    failing re-list is not confirmation.
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
    """Compare remote page metadata to stored rows, with no content download.

    ``provider`` is explicit, never inferred: sync state is per provider, so
    a Google sync never diffs against Notion's rows. ``workspace_id`` None is
    org-wide; each space is diffed on its own.
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

    removed_ids = _removable(
        adapter, [eid for eid in stored if eid not in live_ids], provider=provider
    )
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


def _doc_tags(doc, run_tags: list[str] | None) -> list[str] | None:
    """Union of run-level tags and the document's own (e.g. its Slack channel)."""
    merged = list(run_tags or []) + list(getattr(doc, "tags", None) or [])
    # Stable order, so a re-ingest does not rewrite the column.
    return list(dict.fromkeys(merged)) or None


def _doc_meta(doc) -> tuple[dict, str | None]:
    """``(source_meta, source_editor_key)`` for one fetched document: the
    adapter's record plus links found in the body. ``{}`` (never None) marks
    "captured, nothing found", so the refresh does not pick it up again.
    """
    meta = merge_meta(getattr(doc, "meta", None), links=extract_links(doc.content)) or {}
    return meta, editor_key(meta)


def _doc_access(
    doc, ref: SourceRef | None, provider: str
) -> tuple[bool, list[str] | None, bool] | None:
    """Resolve ``(is_public, viewers, unreadable)``, or ``None`` meaning SKIP.

    The fetched document wins over the listing (fresher). Fail closed: an
    ``ACL_CAPABLE`` provider reporting no access is skipped, never published to
    the scope. ``unreadable`` is explicit: "shared with one person" and "could
    only prove one person" look alike once the list is built.
    """
    access = getattr(doc, "access", None) or getattr(ref, "access", None)
    if access is None:
        if provider in ACL_CAPABLE:
            return None
        return True, None, False
    return access.is_public, list(access.viewers) or None, bool(access.unreadable)


def _restamp_unchanged_access(
    store,
    refs: list[SourceRef],
    handled: set[str],
    *,
    org_id: str,
    provider: str,
    workspace_id: str | None,
) -> int:
    """Refresh the ACL of documents this sync did not re-fetch.

    A permission change moves no content, so without this revocation waits
    for an edit. Uses the listing's sharing (free). Returns how many could not
    be refreshed (they keep their viewers).
    """
    if provider not in ACL_CAPABLE:
        return 0
    pending = [ref for ref in refs if ref.external_id not in handled]
    # An `unreadable` access is a fallback, never written over a real one.
    entries = [
        (ref.external_id, ref.access.is_public, list(ref.access.viewers) or None)
        for ref in pending
        if getattr(ref, "access", None) is not None and not ref.access.unreadable
    ]
    # Frozen, not blanked: an unreadable document keeps its last viewers (a
    # transient error must not empty a corpus). Reported, since a removed
    # viewer keeps access meanwhile.
    frozen = len(pending) - len(entries)
    if frozen:
        logger.warning(
            "Access for %s already-indexed %s document(s) could not be refreshed "
            "(sharing not reported); keeping their existing viewers (org=%s)",
            frozen, provider, org_id,
        )
    if not entries:
        return frozen
    try:
        store.set_source_document_access(
            org_id, provider=provider, entries=entries, workspace_id=workspace_id
        )
    except Exception:  # noqa: BLE001 - never fail a good ingest over a re-stamp
        logger.exception(
            "Failed to refresh document access for %s docs (org=%s provider=%s)",
            len(entries), org_id, provider,
        )
    return frozen


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
    """Re-fetch Slack threads whose stored title has no ``#channel:`` prefix:
    keyword search and chips cannot match a channel without it.
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


def _guard_document(
    guard: InjectionGuard | None, threshold: float, raw_chunks: list[str], *, external_id: str
) -> tuple[list[float | None] | None, bool]:
    """Injection scores for a document's raw chunks, and whether it is flagged.

    Scored before contextualization: a flagged document skips it whole, since
    every call carries the full text. A guard failure is ``(None, False)``.
    """
    if guard is None:
        return None, False
    try:
        scores = guard.score(raw_chunks)
    except Exception:  # noqa: BLE001 - scoring must never fail an ingest job
        logger.warning("Injection scoring failed for %s; left unscored", external_id, exc_info=True)
        return None, False
    flagged = [i for i, sc in enumerate(scores) if sc is not None and sc >= threshold]
    if flagged:
        logger.warning(
            "Injection guard flagged %s chunk(s) of %s (max %.2f); stored without context",
            len(flagged), external_id, max(scores[i] for i in flagged),
        )
    return scores, bool(flagged)


def _store_scores(store: VectorStore, document_id: str, scores, guard: InjectionGuard | None) -> None:
    if guard is None or scores is None:
        return
    try:
        store.set_injection_scores(document_id, dict(enumerate(scores)), guard.model)
    except Exception:  # noqa: BLE001 - a missing score is retried by the backfill
        logger.warning("Could not store injection scores for %s", document_id, exc_info=True)


def _store_tables(document_id, doc, *, org_id: str, workspace_id: str | None) -> None:
    """Hand the document to the dataset adapters (app/doctables), for charts.

    Every adapter runs inline at ingest: stored tables (no AI) and figures in
    prose (one paced aux-model call, only past the cheap figures check). A
    page with no series is marked read. Tables cascade with the document row.
    Never raises: a chart failure must not cost the indexed document.
    """
    from ..doctables import base, factory

    text = base.DocumentText(
        external_id=doc.external_id, title=doc.title, content=doc.content or "",
        tables=tuple(doc.tables) if doc.tables is not None else None,
    )
    try:
        adapters = factory.build_dataset_adapters()
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("Could not build table readers for %s", doc.external_id, exc_info=True)
        adapters = []
    retry_later = False
    for adapter in adapters:
        try:
            if not adapter.wants(text):
                continue
            from ..doctables.store import replace_document_tables

            tables = adapter.extract(text)
            if tables:
                replace_document_tables(
                    document_id, org_id=org_id, workspace_id=workspace_id,
                    tables=tables, origin=adapter.origin,
                )
            elif adapter.origin == "text":
                from ..doctables.queue import mark_read

                mark_read(document_id, org_id=org_id, workspace_id=workspace_id,
                          external_id=doc.external_id, title=doc.title or "")
        except Exception as exc:  # noqa: BLE001 - see docstring; Chart mode can still read it
            from ..core.exceptions import LLMRateLimitError
            from ..doctables.text_adapter import UnreadableReply

            if isinstance(exc, UnreadableReply) and _unreadable_again(document_id, doc, org_id,
                                                                       workspace_id):
                # A cut or garbled reply says nothing about the page: read it
                # again on a later sync, a few times, never "no figures".
                retry_later = True
            elif isinstance(exc.__cause__, LLMRateLimitError):
                # The quota, not this document: left unchecked so the next
                # sync's backfill reads it once the quota is back.
                retry_later = True
                logger.warning("Model quota spent; %s tables of %s left for a later sync",
                               adapter.origin, doc.external_id)
            else:
                logger.warning("Could not store %s tables of %s", adapter.origin,
                               doc.external_id, exc_info=True)
    if retry_later:
        return
    try:
        # Stamped on other failures too, or one bad document holds a
        # backfill slot forever.
        from ..doctables.store import mark_checked

        mark_checked(document_id)
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("Could not stamp tables check of %s", doc.external_id, exc_info=True)


#: Reads of one page whose reply was unusable before it is left as checked.
MAX_UNREADABLE_READS = 3


def _unreadable_again(document_id, doc, org_id, workspace_id) -> bool:
    """Count this unusable read; True while the page should be read again."""
    try:
        from ..doctables.queue import note_unreadable

        attempts = note_unreadable(document_id, org_id=org_id, workspace_id=workspace_id,
                                   external_id=doc.external_id, title=doc.title or "")
    except Exception:  # noqa: BLE001 - uncounted means one more read, not a lost page
        logger.warning("Could not count an unreadable read of %s", doc.external_id, exc_info=True)
        return True
    logger.warning("Unreadable figures reply for %s (read %d of %d)", doc.external_id,
                   attempts, MAX_UNREADABLE_READS)
    return attempts < MAX_UNREADABLE_READS


def backfill_tables(
    adapter: SourceAdapter,
    *,
    org_id: str,
    provider: str,
    workspace_id: str | None = None,
    skip_ids: set[str] | None = None,
    live_ids: set[str] | None = None,
    batch: int | None = None,
) -> int:
    """Read tables from already-indexed documents, a bounded batch per sync.

    Unchanged documents are never re-fetched, so this re-fetches up to
    ``DOCTABLES_BACKFILL_BATCH`` unchecked ones for ``_store_tables`` (tables
    only, no re-embedding). Never raises; a refused fetch stays unchecked,
    and ``live_ids`` skips documents the source no longer lists.
    """
    from ..config.settings import DocTablesSettings
    from ..doctables import store as table_store

    limit = DocTablesSettings.from_env().backfill_batch if batch is None else batch
    if limit <= 0:
        return 0
    skip_ids = skip_ids or set()
    try:
        candidates = table_store.list_unchecked(
            org_id=org_id, provider=provider, workspace_id=workspace_id,
            limit=limit + len(skip_ids) + 50,
        )
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("backfill_tables: listing failed for %s", provider, exc_info=True)
        return 0
    eligible = [
        (doc_id, ext) for doc_id, ext in candidates
        if ext not in skip_ids and (live_ids is None or ext in live_ids)
    ]
    done = 0
    for document_id, external_id in eligible[:limit]:
        try:
            doc = adapter.fetch_document(external_id)
        except Exception:  # noqa: BLE001 - see docstring
            logger.info("backfill_tables: could not fetch %s/%s", provider, external_id)
            continue
        _store_tables(document_id, doc, org_id=org_id, workspace_id=workspace_id)
        done += 1
    if done:
        logger.info("backfill_tables: read %d %s document(s)", done, provider)
    return done


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
    guard: InjectionGuard | None = None,
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
    guard = guard or build_injection_guard()
    guard_threshold = GuardSettings.from_env().threshold
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

    removed_ids = _removable(adapter, removed_ids, provider=provider)
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
            "removed. (listed=%d stored=%d)",
            org_id, provider, workspace_id, len(refs), len(stored),
        )

    removed_n = (
        store.delete_source_documents(org_id, provider, removed_ids, workspace_id=workspace_id)
        if removed_ids
        else 0
    )

    chunks_total = 0
    skipped = 0
    permission_unreadable = 0
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
        access = _doc_access(doc, ref, provider)
        if access is None:
            # Fail closed: no sharing and no account that can open it. Counted
            # and logged, so it never reads as an empty source.
            logger.warning(
                "Skipping %s/%s: %s reports no sharing information for it and no "
                "account we could fall back to",
                provider, ref.external_id, provider,
            )
            skipped += 1
            permission_unreadable += 1
            report("indexing", done, total_work)
            continue
        is_public, viewers, unreadable = access
        if unreadable and is_update:
            # Already indexed: freeze as `_restamp_unchanged_access` does. Its
            # real access set beats the fallback, and the content update waits
            # until the audience is readable.
            logger.warning(
                "%s/%s changed but its sharing is no longer reported; keeping the "
                "access set and the content it already had",
                provider, ref.external_id,
            )
            permission_unreadable += 1
            report("indexing", done, total_work)
            continue
        if unreadable:
            # New: indexed for the connected account only, and counted for
            # the connection card.
            logger.info(
                "%s/%s: sharing not reported; indexing it for the connected "
                "account only (%s)",
                provider, ref.external_id, ", ".join(viewers or []) or "nobody",
            )
            permission_unreadable += 1
        meta, meta_editor = _doc_meta(doc)
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
                tags=_doc_tags(doc, tags),
                last_editor=doc.last_editor or ref.last_editor,
                is_public=is_public,
                viewers=viewers,
                source_meta=meta,
                editor_key=meta_editor,
            )
            skipped += 1
            report("indexing", done, total_work)
            continue

        scores, flagged = _guard_document(
            guard, guard_threshold, raw_chunks, external_id=doc.external_id
        )
        if apply_contextual_inline and llm is not None and not flagged:
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
            tags=_doc_tags(doc, tags),
            last_editor=doc.last_editor or ref.last_editor,
            is_public=is_public,
            viewers=viewers,
            source_meta=meta,
            editor_key=meta_editor,
        )
        _store_scores(store, document_id, scores, guard)
        _store_tables(document_id, doc, org_id=org_id, workspace_id=workspace_id)
        doc_ids.append(document_id)
        ingested_external_ids.append(doc.external_id)
        chunks_total += len(chunks)
        if is_update:
            updated_n += 1
        else:
            added_n += 1
        report("indexing", done, total_work)

    # Revocation: refs not re-fetched still get their access from the listing.
    permission_unreadable += _restamp_unchanged_access(
        store,
        refs,
        {r.external_id for r, _ in work},
        org_id=org_id,
        provider=provider,
        workspace_id=workspace_id,
    )

    meta_refreshed: list[str] = []
    refresh_missing_meta(
        adapter,
        store,
        refreshed=meta_refreshed,
        org_id=org_id,
        provider=provider,
        workspace_id=workspace_id,
        skip_ids={r.external_id for r, _ in work},
        live_ids={r.external_id for r in refs},
    )
    backfill_tables(
        adapter,
        org_id=org_id,
        provider=provider,
        workspace_id=workspace_id,
        skip_ids={r.external_id for r, _ in work},
        live_ids={r.external_id for r in refs},
    )

    return IngestResult(
        documents_ingested=added_n + updated_n,
        documents_added=added_n,
        documents_updated=updated_n,
        documents_removed=removed_n,
        documents_unchanged=unchanged,
        chunks_stored=chunks_total,
        documents_skipped=skipped,
        documents_permission_unreadable=permission_unreadable,
        document_ids=doc_ids,
        ingested_external_ids=ingested_external_ids,
        meta_refreshed_external_ids=meta_refreshed,
    )


def refresh_missing_meta(
    adapter: SourceAdapter,
    store: VectorStore,
    *,
    org_id: str,
    provider: str,
    workspace_id: str | None = None,
    skip_ids: set[str] | None = None,
    live_ids: set[str] | None = None,
    batch: int | None = None,
    refreshed: list[str] | None = None,
) -> int:
    """Capture ``source_meta`` for already-indexed documents, a bounded batch per run.

    Unchanged documents are never re-fetched, so this re-fetches up to
    ``GRAPH_META_REFRESH_BATCH`` (newest first) and writes metadata only.
    Never raises; a failure stays NULL for a later job, and ``live_ids``
    skips documents the source no longer lists.
    """
    limit = GraphSettings.from_env().meta_refresh_batch if batch is None else batch
    if limit <= 0:
        return 0
    skip_ids = skip_ids or set()
    try:
        candidates = store.list_source_documents_missing_meta(
            org_id, provider=provider, workspace_id=workspace_id,
            limit=limit + len(skip_ids) + 50,
        )
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("refresh_missing_meta: listing failed for %s", provider, exc_info=True)
        return 0
    entries: list[tuple[str, dict, str | None]] = []
    eligible = [
        c for c in candidates
        if c not in skip_ids and (live_ids is None or c in live_ids)
    ]
    for external_id in eligible[:limit]:
        try:
            doc = adapter.fetch_document(external_id)
        except Exception:  # noqa: BLE001 - see docstring
            logger.info("refresh_missing_meta: could not fetch %s/%s", provider, external_id)
            continue
        meta, meta_editor = _doc_meta(doc)
        entries.append((external_id, meta, meta_editor))
    if not entries:
        return 0
    try:
        written = store.set_source_document_meta(
            org_id, provider=provider, entries=entries, workspace_id=workspace_id
        )
        if refreshed is not None:
            refreshed.extend(e[0] for e in entries)
        return written
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("refresh_missing_meta: write failed for %s", provider, exc_info=True)
        return 0


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
    guard: InjectionGuard | None = None,
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
    guard = guard or build_injection_guard()
    guard_threshold = GuardSettings.from_env().threshold

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
            # A flagged document keeps its plain, already-scored rows.
            scores, flagged = _guard_document(
                guard, guard_threshold, raw_chunks, external_id=external_id
            )
            if flagged:
                report("enriching", i, total)
                continue
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
            # Chunks only: the documents row keeps the access, tags and editor
            # ingest wrote. Re-upserting it re-published restricted files to
            # the whole scope.
            document_id = store.replace_source_document_chunks(
                org_id,
                provider=provider,
                external_id=doc.external_id,
                chunks=chunks,
                embeddings=embeddings,
                workspace_id=workspace_id,
            )
            if document_id is not None:
                # Replacing the chunk rows dropped their ingest-time scores.
                _store_scores(store, document_id, scores, guard)
                enriched += 1
        except Exception:  # noqa: BLE001 - one bad page must not abort enrich
            pass
        report("enriching", i, total)
    return enriched
