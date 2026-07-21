"""Document ingestion helpers: preprocessing and chunking.

Public API:
    from app.ingestion import preprocess, chunk_text
    chunks = chunk_text(preprocess(raw_text))
"""

from .preprocessing import preprocess
from .chunking import chunk_text
from .pipeline import ingest_source, IngestResult

__all__ = ["preprocess", "chunk_text", "ingest_source", "IngestResult"]
