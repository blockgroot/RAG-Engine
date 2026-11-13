"""Local cross-encoder reranker via sentence-transformers ``CrossEncoder``.

Default ``BAAI/bge-reranker-v2-m3``: multilingual, in-process (no key, data
stays local). Lighter models fit behind the same interface.
"""

from __future__ import annotations

from dataclasses import replace

from ..core.exceptions import ConfigurationError, ProviderError
from ..vectorstore.base import RetrievedChunk
from .base import Reranker

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


class CrossEncoderReranker(Reranker):
    """Rerank candidates with an in-process cross-encoder."""

    def __init__(self, model: str | None = None, device: str | None = None) -> None:
        self.model_name = model or DEFAULT_MODEL
        self.device = device or None

        import os

        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("MKL_NUM_THREADS", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise ConfigurationError(
                "sentence-transformers is not installed. Run: pip install -r requirements.txt",
                cause=exc,
            ) from exc

        try:
            self._model = CrossEncoder(self.model_name, device=self.device)
        except Exception as exc:  # model download / load failure
            raise ProviderError(
                f"Failed to load reranker model {self.model_name!r}", cause=exc
            ) from exc

    def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []

        pairs = [(query, c.content) for c in candidates]
        try:
            scores = self._model.predict(pairs)
        except Exception as exc:
            raise ProviderError(
                f"Reranking failed with {self.model_name!r}", cause=exc
            ) from exc

        # Order candidates by cross-encoder score (desc); keep the chunks intact
        # (their cosine `.score` is preserved for the downstream confidence gate).
        ranked = sorted(
            zip(candidates, scores), key=lambda pair: float(pair[1]), reverse=True
        )
        return [replace(chunk, rerank_score=float(s)) for chunk, s in ranked[:top_k]]
