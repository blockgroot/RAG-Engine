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

The ``Agent`` interface is generic on purpose, and that finally pays off with
``GitHubAgent``: three implementations now exist, and they do NOT share a shape.
``PolicyAgent`` and ``WorkspaceAgent`` are thin adapters over a ``RagPipeline``
(retrieve → gate → grounded generate) via ``RagPipelineAgent``. ``GitHubAgent``
embeds nothing and answers purely from live, bounded GitHub API tool-calls — so
it implements ``Agent`` directly, with no pipeline behind it. The interface being
source-agnostic is what lets ``app/api/chat.py`` pick between them without
knowing any of that.
"""

from .base import Agent, AgentResponse, Citation
from .github_agent import GitHubAgent
from .policy_agent import PolicyAgent
from .rag_pipeline_agent import RagPipelineAgent
from .workspace_agent import WorkspaceAgent
from .factory import build_github_agent, build_policy_agent, build_workspace_agent

__all__ = [
    "Agent",
    "AgentResponse",
    "Citation",
    "GitHubAgent",
    "PolicyAgent",
    "RagPipelineAgent",
    "WorkspaceAgent",
    "build_github_agent",
    "build_policy_agent",
    "build_workspace_agent",
]
