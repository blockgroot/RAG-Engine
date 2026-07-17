"""Local, in-process embedding provider using sentence-transformers.

Unlike ``EmbeddingProvider`` (which is an HTTP client for an OpenAI-compatible
endpoint), this loads the model directly into the Python process. No server, no
API key, no per-token cost, and document text never leaves the machine — which
matters for multi-tenant policy data.

It exposes the same ``embed(texts) -> list[list[float]]`` method so it is a
drop-in replacement for the HTTP embedding provider from the caller's side.
"""

from __future__ import annotations

import os

from .exceptions import ConfigurationError, EmbeddingProviderError

# BGE-M3 default. Overridable via EMBEDDING_MODEL.
DEFAULT_MODEL = "BAAI/bge-m3"


class LocalEmbeddingProvider:
    """Embed text locally with a sentence-transformers model.

    Configuration is read from constructor arguments, falling back to
    environment variables:

    - ``EMBEDDING_MODEL``  (default ``BAAI/bge-m3``)
    - ``EMBEDDING_DEVICE`` (optional: ``cpu``, ``cuda``, ``mps``; auto if unset)

    The model is downloaded once (cached under the Hugging Face cache dir) and
    loaded into memory at construction time.
    """

    def __init__(
        self,
        model: str | None = None,
        device: str | None = None,
        normalize: bool = True,
    ) -> None:
        self.model_name = model or os.getenv("EMBEDDING_MODEL") or DEFAULT_MODEL
        self.device = device or os.getenv("EMBEDDING_DEVICE") or None
        self.normalize = normalize

        if not self.model_name:
            raise ConfigurationError("Missing required embedding config: EMBEDDING_MODEL")

        # Import lazily so the rest of the app doesn't pay the (heavy) import
        # cost unless local embeddings are actually used.
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
        """Embed a list of texts and return one vector per input, in order.

        Raises ``EmbeddingProviderError`` if encoding fails.
        """
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
