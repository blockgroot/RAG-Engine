"""Document ingestion helpers: preprocessing and chunking.

Public API:
    from app.ingestion import preprocess, chunk_text
    chunks = chunk_text(preprocess(raw_text))
"""

from .preprocessing import preprocess
from .chunking import chunk_text

__all__ = ["preprocess", "chunk_text"]
