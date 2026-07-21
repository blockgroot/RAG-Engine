"""Ingestion orchestrator: pull from a source, store into the vector store.

Wires a ``SourceAdapter`` (Phase 4) into the pieces already built:
list documents → fetch each → ``preprocess`` + ``chunk_text`` (Phase 2) → embed
(Phase 1 ``EmbeddingProvider``) → ``store.add_document`` scoped to one ``org_id``
(Phase 2 ``VectorStore``).

Like ``app.rag`` this is an *orchestrator*, not a swappable provider: it only
composes existing interfaces, so it has no ``base.py``. Providers/adapter are
injected (defaulting from factories) so it stays pure and testable and the
format-specific work stays inside the adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config.settings import ChunkingSettings
from ..embeddings import build_embedding_provider
from ..embeddings.base import EmbeddingProvider
from ..ingestion.chunking import chunk_text
from ..ingestion.preprocessing import preprocess
from ..sources.base import SourceAdapter
from ..vectorstore import build_vector_store
from ..vectorstore.base import VectorStore


@dataclass(frozen=True)
class IngestResult:
    """Summary of one ingestion run for a single org."""

    documents_ingested: int = 0
    chunks_stored: int = 0
    documents_skipped: int = 0  # fetched but had no usable text
    document_ids: list[str] = field(default_factory=list)


def ingest_source(
    adapter: SourceAdapter,
    org_id: str,
    *,
    embedder: EmbeddingProvider | None = None,
    store: VectorStore | None = None,
    chunking: ChunkingSettings | None = None,
) -> IngestResult:
    """Ingest every document the ``adapter`` exposes into ``org_id``.

    Each document is preprocessed, chunked, embedded, and stored. Documents that
    produce no chunks (e.g. an empty page) are counted as skipped, not stored.
    """
    embedder = embedder or build_embedding_provider()
    store = store or build_vector_store()

    documents = 0
    chunks_total = 0
    skipped = 0
    doc_ids: list[str] = []

    for ref in adapter.list_documents():
        doc = adapter.fetch_document(ref.external_id)
        chunks = chunk_text(preprocess(doc.content), chunking)
        if not chunks:
            skipped += 1
            continue

        embeddings = embedder.embed(chunks)
        document_id = store.add_document(
            org_id=org_id,
            title=doc.title,
            chunks=chunks,
            embeddings=embeddings,
            source_uri=doc.source_uri,
        )
        documents += 1
        chunks_total += len(chunks)
        doc_ids.append(document_id)

    return IngestResult(
        documents_ingested=documents,
        chunks_stored=chunks_total,
        documents_skipped=skipped,
        document_ids=doc_ids,
    )
