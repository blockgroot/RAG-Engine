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

RAG agents (policy, workspace and the per-source ones) are adapters over
``RagPipeline`` via ``RagPipelineAgent``; ``GitHubAgent`` (live reads) and
``InsightsAgent`` (SQL charts) implement ``Agent`` directly.
"""

from .base import Agent, AgentResponse, Citation
from .drive_agent import DriveAgent
from .github_agent import GitHubAgent
from .linear_agent import LinearAgent
from .notion_agent import NotionAgent
from .policy_agent import PolicyAgent
from .rag_pipeline_agent import RagPipelineAgent
from .slack_agent import SlackAgent
from .workspace_agent import WorkspaceAgent
from .insights_agent import InsightsAgent
from .factory import (
    build_drive_agent,
    build_github_agent,
    build_insights_agent,
    build_linear_agent,
    build_notion_agent,
    build_policy_agent,
    build_slack_agent,
    build_workspace_agent,
)

__all__ = [
    "Agent",
    "AgentResponse",
    "Citation",
    "DriveAgent",
    "GitHubAgent",
    "InsightsAgent",
    "LinearAgent",
    "NotionAgent",
    "PolicyAgent",
    "RagPipelineAgent",
    "SlackAgent",
    "WorkspaceAgent",
    "build_drive_agent",
    "build_github_agent",
    "build_insights_agent",
    "build_linear_agent",
    "build_notion_agent",
    "build_policy_agent",
    "build_slack_agent",
    "build_workspace_agent",
]
