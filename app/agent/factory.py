"""Single construction point for agents.

A ready ``RagPipeline`` may be injected; otherwise one is built from config
via ``build_rag_pipeline`` with any of its keyword arguments forwarded.
Pinned-corpus agents (workspace, Slack, Linear, Notion, Drive) get their own
pipeline with web search OFF: their content is either present or not.
"""

from __future__ import annotations

from dataclasses import replace

from ..config.settings import (
    DriveAgentSettings,
    GitHubAgentSettings,
    LinearAgentSettings,
    NotionAgentSettings,
    RagSettings,
    SlackAgentSettings,
    WorkspaceAgentSettings,
)
from ..llm import build_llm_provider
from ..rag import RagPipeline, build_rag_pipeline
from ..rag.prompts import (
    DRIVE_PROMPT_PROFILE,
    LINEAR_PROMPT_PROFILE,
    NOTION_PROMPT_PROFILE,
    SLACK_PROMPT_PROFILE,
    WORKSPACE_PROMPT_PROFILE,
)
from .drive_agent import DriveAgent
from .github_agent import GitHubAgent
from .linear_agent import LinearAgent
from .notion_agent import NotionAgent
from .policy_agent import PolicyAgent
from .slack_agent import SlackAgent
from .insights_agent import InsightsAgent
from .workspace_agent import WorkspaceAgent


def build_policy_agent(
    pipeline: RagPipeline | None = None, **pipeline_kwargs
) -> PolicyAgent:
    """Build a ``PolicyAgent``, defaulting its pipeline from configuration."""
    return PolicyAgent(pipeline or build_rag_pipeline(**pipeline_kwargs))


def build_workspace_agent(
    pipeline: RagPipeline | None = None, **pipeline_kwargs
) -> WorkspaceAgent:
    """Build a ``WorkspaceAgent``. Same RAG settings as policy; only the
    fallback, framing and web-search-off differ (overridable via
    ``pipeline_kwargs``)."""
    if pipeline is None:
        pipeline_kwargs.setdefault(
            "settings",
            replace(
                RagSettings.from_env(),
                fallback_response=WorkspaceAgentSettings.from_env().fallback_response,
            ),
        )
        pipeline_kwargs.setdefault("prompt_profile", WORKSPACE_PROMPT_PROFILE)
        pipeline_kwargs.setdefault("web_search", None)
        pipeline = build_rag_pipeline(**pipeline_kwargs)
    return WorkspaceAgent(pipeline)


def build_slack_agent(
    pipeline: RagPipeline | None = None, **pipeline_kwargs
) -> SlackAgent:
    """Build a ``SlackAgent``: the pipeline is pinned to
    ``source_provider="slack"``. Web search off: "what did we decide in #eng?"
    has no public-web answer."""
    if pipeline is None:
        pipeline_kwargs.setdefault(
            "settings",
            replace(
                RagSettings.from_env(),
                fallback_response=SlackAgentSettings.from_env().fallback_response,
            ),
        )
        pipeline_kwargs.setdefault("prompt_profile", SLACK_PROMPT_PROFILE)
        pipeline_kwargs.setdefault("web_search", None)
        pipeline_kwargs.setdefault("source_provider", "slack")
        pipeline = build_rag_pipeline(**pipeline_kwargs)
    return SlackAgent(pipeline)


def build_linear_agent(
    pipeline: RagPipeline | None = None, **pipeline_kwargs
) -> LinearAgent:
    """Build a ``LinearAgent``, pinned to ``source_provider="linear"``, web
    search off."""
    if pipeline is None:
        pipeline_kwargs.setdefault(
            "settings",
            replace(
                RagSettings.from_env(),
                fallback_response=LinearAgentSettings.from_env().fallback_response,
            ),
        )
        pipeline_kwargs.setdefault("prompt_profile", LINEAR_PROMPT_PROFILE)
        pipeline_kwargs.setdefault("web_search", None)
        pipeline_kwargs.setdefault("source_provider", "linear")
        pipeline = build_rag_pipeline(**pipeline_kwargs)
    return LinearAgent(pipeline)


def build_notion_agent(
    pipeline: RagPipeline | None = None, **pipeline_kwargs
) -> NotionAgent:
    """Build a ``NotionAgent``, pinned to ``source_provider="notion"`` so a
    Notion answer is never grounded on a Drive chunk. Web search off."""
    if pipeline is None:
        pipeline_kwargs.setdefault(
            "settings",
            replace(
                RagSettings.from_env(),
                fallback_response=NotionAgentSettings.from_env().fallback_response,
            ),
        )
        pipeline_kwargs.setdefault("prompt_profile", NOTION_PROMPT_PROFILE)
        pipeline_kwargs.setdefault("web_search", None)
        pipeline_kwargs.setdefault("source_provider", "notion")
        pipeline = build_rag_pipeline(**pipeline_kwargs)
    return NotionAgent(pipeline)


def build_drive_agent(
    pipeline: RagPipeline | None = None, **pipeline_kwargs
) -> DriveAgent:
    """Build a ``DriveAgent``, pinned to ``source_provider="google"``."""
    if pipeline is None:
        pipeline_kwargs.setdefault(
            "settings",
            replace(
                RagSettings.from_env(),
                fallback_response=DriveAgentSettings.from_env().fallback_response,
            ),
        )
        pipeline_kwargs.setdefault("prompt_profile", DRIVE_PROMPT_PROFILE)
        pipeline_kwargs.setdefault("web_search", None)
        pipeline_kwargs.setdefault("source_provider", "google")
        pipeline = build_rag_pipeline(**pipeline_kwargs)
    return DriveAgent(pipeline)


def build_github_agent(
    llm=None,
    reader_builder=None,
    settings: GitHubAgentSettings | None = None,
) -> GitHubAgent:
    """Build a ``GitHubAgent``: no pipeline, just an LLM and a reader builder.

    ``reader_builder`` defaults to ``githublive.build_github_reader``, imported
    lazily; a builder because a reader is tenant-scoped, built per request.
    """
    if reader_builder is None:
        from ..githublive import build_github_reader

        reader_builder = build_github_reader

    resolved = settings or GitHubAgentSettings.from_env()
    return GitHubAgent(
        llm=llm or build_llm_provider(),
        reader_builder=reader_builder,
        fallback_response=resolved.fallback_response,
        settings=resolved,
    )


def build_insights_agent() -> InsightsAgent:
    """No pipeline — charts are SQL, like GitHub is live reads."""
    return InsightsAgent()
