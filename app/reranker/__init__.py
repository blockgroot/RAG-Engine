"""Cross-encoder reranking (Phase 6).

Public API:
    from app.reranker import build_reranker
    reranker = build_reranker()
    top = reranker.rerank("part-time leave", candidates, top_k=5)
"""

from .base import Reranker
from .local import CrossEncoderReranker
from .factory import build_reranker

__all__ = ["Reranker", "CrossEncoderReranker", "build_reranker"]
