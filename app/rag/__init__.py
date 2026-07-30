"""The RAG query path: grounded, tenant-scoped question answering.

Public API::

    from app.rag import build_rag_pipeline
    rag = build_rag_pipeline()
    result = rag.answer("How many days of paid leave do we get?", org_id)
    if result.answered:
        print(result.answer)          # grounded answer
        print(result.sources)         # chunks it was grounded on (this org only)
    else:
        print(result.answer)          # the fixed "I don't have information" fallback
"""

from .pipeline import RagPipeline, RagResult
from .prompts import build_grounded_prompt
from .factory import build_rag_pipeline
from .summary_fold import (
    shutdown_summary_folds,
    wait_for_pending_summary_folds,
)

__all__ = [
    "RagPipeline",
    "RagResult",
    "build_grounded_prompt",
    "build_rag_pipeline",
    "shutdown_summary_folds",
    "wait_for_pending_summary_folds",
]
