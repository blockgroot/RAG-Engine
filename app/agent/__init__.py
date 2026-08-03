"""Agents: a question + tenant in, a structured, source-attributed answer out.

Public API::

    from app.agent import build_policy_agent
    agent = build_policy_agent()
    response = agent.answer("How many days of paid leave do we get?", org_id)
    if response.grounded:
        print(response.answer)        # grounded answer (policy or web)
        print(response.source)        # "policy" | "web"
        print(response.citations)     # evidence it was grounded on
    else:
        print(response.answer)        # the fixed "I don't have information" fallback

The ``Agent`` interface is generic on purpose — a future GitHub agent implements
the same contract. ``PolicyAgent`` is the one implementation today, composing the
Phase 3–6 RAG pipeline.
"""

from .base import Agent, AgentResponse, Citation
from .policy_agent import PolicyAgent
from .rag_pipeline_agent import RagPipelineAgent
from .workspace_agent import WorkspaceAgent
from .factory import build_policy_agent, build_workspace_agent

__all__ = [
    "Agent",
    "AgentResponse",
    "Citation",
    "PolicyAgent",
    "RagPipelineAgent",
    "WorkspaceAgent",
    "build_policy_agent",
    "build_workspace_agent",
]
