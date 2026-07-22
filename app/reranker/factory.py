"""Single construction point for the reranker."""

from __future__ import annotations

from ..config.settings import RerankerSettings
from .base import Reranker
from .local import CrossEncoderReranker


def build_reranker(settings: RerankerSettings | None = None) -> Reranker:
    """Build the configured reranker (local cross-encoder by default)."""
    settings = settings or RerankerSettings.from_env()
    return CrossEncoderReranker(model=settings.model, device=settings.device)
