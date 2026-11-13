"""The Workspace Agent: a generic agent for one workspace's own content.

Its own ``RagPipeline`` with ``WORKSPACE_PROMPT_PROFILE`` and fallback, so a
space's content is not framed as HR policy; the gate and retrieval are the
shared machinery. Web search off: a space's content is present or not.
Chosen over ``PolicyAgent`` whenever a request carries a ``workspace_id``.
"""

from __future__ import annotations

from .rag_pipeline_agent import RagPipelineAgent


class WorkspaceAgent(RagPipelineAgent):
    """Answers questions scoped to one sub-workspace's own connected content."""
