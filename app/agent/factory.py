"""Single construction point for agents.

Callers do ``build_policy_agent()`` and get a fully wired ``PolicyAgent``. A ready
``RagPipeline`` may be injected to reuse one (tests, an HTTP layer); otherwise a
pipeline is built from configuration via ``build_rag_pipeline``, and any of its
keyword arguments (``memory``, ``web_search``, ``retriever``, ``embedder`` …) may
be forwarded — so a caller can turn a capability off exactly as before.
"""

from __future__ import annotations

from ..rag import RagPipeline, build_rag_pipeline
from .policy_agent import PolicyAgent


def build_policy_agent(
    pipeline: RagPipeline | None = None, **pipeline_kwargs
) -> PolicyAgent:
    """Build a ``PolicyAgent``, defaulting its pipeline from configuration."""
    return PolicyAgent(pipeline or build_rag_pipeline(**pipeline_kwargs))
