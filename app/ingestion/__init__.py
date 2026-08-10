"""Document ingestion helpers: preprocessing, chunking, and source ingest.

Public API:
    from app.ingestion import preprocess, chunk_text, ingest_source, detect_source_changes
"""

from .preprocessing import preprocess
from .chunking import chunk_text
from .pipeline import (
    ChangeReport,
    IngestResult,
    detect_source_changes,
    enrich_source_contextual,
    ingest_source,
)

__all__ = [
    "preprocess",
    "chunk_text",
    "ingest_source",
    "enrich_source_contextual",
    "detect_source_changes",
    "IngestResult",
    "ChangeReport",
]
