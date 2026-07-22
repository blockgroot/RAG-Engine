"""Local cross-encoder reranker via sentence-transformers ``CrossEncoder``.

Default model ``BAAI/bge-reranker-v2-m3`` — same open-source family as our BGE-M3
embeddings, strong quality, multilingual, and it runs in-process ($0, no API key,
data stays local), consistent with the project's self-hosted principle. Reuses the
``sentence-transformers`` dependency we already have, so Phase 6 adds no new dep.
Lighter swaps (``BAAI/bge-reranker-base``, ``cross-encoder/ms-marco-MiniLM-L-6-v2``)
fit behind this same interface if latency matters more than quality.
"""

from __future__ import annotations

from ..core.exceptions import ConfigurationError, ProviderError
from ..vectorstore.base import RetrievedChunk
from .base import Reranker

DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


class CrossEncoderReranker(Reranker):
    """Rerank candidates with an in-process cross-encoder."""

    def __init__(self, model: str | None = None, device: str | None = None) -> None:
        self.model_name = model or DEFAULT_MODEL
        self.device = device or None

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
        return [chunk for chunk, _ in ranked[:top_k]]
