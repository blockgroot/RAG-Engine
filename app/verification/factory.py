"""Single construction point for the answer verifier (Phase 10).

Callers do ``build_verifier()`` and get back something satisfying the
``Verifier`` interface. Adding an alternate verification technique (e.g. a
dedicated NLI cross-encoder, or an LLM-judge verifier) later means adding a
branch here keyed on a ``VERIFIER_BACKEND``-style setting — callers don't change.
Only one backend exists today, so there is no such setting yet; this factory
still exists so the pipeline never constructs a concrete verifier itself.
"""

from __future__ import annotations

from ..config.settings import VerificationSettings
from ..embeddings.base import EmbeddingProvider
from .base import Verifier
from .embedding_similarity import EmbeddingSimilarityVerifier


def build_verifier(
    embedder: EmbeddingProvider, settings: VerificationSettings | None = None
) -> Verifier:
    """Build the configured verifier, reusing an already-built embedder.

    Reusing the caller's ``EmbeddingProvider`` instance (rather than building a
    new one) avoids loading the embedding model twice.
    """
    settings = settings or VerificationSettings.from_env()
    return EmbeddingSimilarityVerifier(
        embedder=embedder, similarity_threshold=settings.similarity_threshold
    )
