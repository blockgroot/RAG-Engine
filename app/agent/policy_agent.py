"""The Policy Agent: company-policy Q&A over a ``RagPipeline`` with the
default ``PromptProfile`` (mapping shared via ``RagPipelineAgent``). Build
via ``factory.build_policy_agent``."""

from __future__ import annotations

from .rag_pipeline_agent import RagPipelineAgent


class PolicyAgent(RagPipelineAgent):
    """Answers policy questions for a tenant by delegating to a ``RagPipeline``."""
