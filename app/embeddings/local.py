"""Local, in-process embedding provider using sentence-transformers.

Loads the model directly into the Python process: no server, no API key, no
per-token cost, and document text never leaves the machine — which matters for
multi-tenant policy data.
"""

from __future__ import annotations

from ..core.exceptions import ConfigurationError, EmbeddingProviderError
from .base import EmbeddingProvider

DEFAULT_MODEL = "BAAI/bge-m3"


class LocalEmbeddingProvider(EmbeddingProvider):
    """Embed text locally with a sentence-transformers model.

    The model is downloaded once (cached under the Hugging Face cache dir) and
    loaded into memory at construction time.
    """

    def __init__(
        self,
        model: str | None = None,
        device: str | None = None,
        normalize: bool = True,
    ) -> None:
        self.model_name = model or DEFAULT_MODEL
        self.device = device or None
        self.normalize = normalize

        if not self.model_name:
            raise ConfigurationError("Missing required embedding config: EMBEDDING_MODEL")

        # Cap BLAS/tokenizer threads before torch loads. Unbounded OpenMP on
        # Apple Silicon + two large models is a common "whole Mac freezes"
        # pattern while the process is still technically making progress.
        import os

        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("MKL_NUM_THREADS", "1")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

        # Import lazily so the app doesn't pay the (heavy) import cost unless
        # local embeddings are actually used.
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ConfigurationError(
                "sentence-transformers is not installed. "
                "Run: pip install -r requirements.txt",
                cause=exc,
            ) from exc

        try:
            self._model = SentenceTransformer(self.model_name, device=self.device)
        except Exception as exc:  # model download / load failure
            raise EmbeddingProviderError(
                f"Failed to load embedding model {self.model_name!r}", cause=exc
            ) from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            vectors = self._model.encode(
                texts,
                normalize_embeddings=self.normalize,
                convert_to_numpy=True,
            )
        except Exception as exc:
            raise EmbeddingProviderError(
                f"Failed to encode {len(texts)} text(s) with {self.model_name!r}",
                cause=exc,
            ) from exc

        return vectors.tolist()
