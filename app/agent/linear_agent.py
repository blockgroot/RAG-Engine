"""The Linear Agent: answers only from this tenant's ingested Linear issues.

Like ``SlackAgent``: pinned to ``source_provider="linear"`` with its own
``LINEAR_PROMPT_PROFILE`` and ``source`` label. No recency retry: an issue
is a self-contained unit, so similarity retrieval suffices.
"""

from __future__ import annotations

from .rag_pipeline_agent import RagPipelineAgent


class LinearAgent(RagPipelineAgent):
    """Answers questions from ingested Linear issues only."""
