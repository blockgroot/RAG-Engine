"""Cross-encoder reranking (Phase 6).

Public API:
    from app.reranker import build_reranker
    reranker = build_reranker()
    top = reranker.rerank("part-time leave", candidates, top_k=5)
"""

from .base import Reranker
from .local import CrossEncoderReranker
from .remote import RemoteReranker
from .factory import build_reranker, clear_reranker_cache

__all__ = [
    "Reranker",
    "CrossEncoderReranker",
    "RemoteReranker",
    "build_reranker",
    "clear_reranker_cache",
]
