"""Single construction point for agents.

Callers do ``build_policy_agent()`` and get a fully wired ``PolicyAgent``. A ready
``RagPipeline`` may be injected to reuse one (tests, an HTTP layer); otherwise a
pipeline is built from configuration via ``build_rag_pipeline``, and any of its
keyword arguments (``memory``, ``web_search``, ``retriever``, ``embedder`` …) may
be forwarded — so a caller can turn a capability off exactly as before.

``build_workspace_agent`` builds the same shape of thing for ``WorkspaceAgent``,
but defaults to a second, independent pipeline: the workspace ``PromptProfile``
+ fallback string (``WorkspaceAgentSettings``), and web-search OFF (a personal
workspace's own content is either present or it isn't — see ``workspace_agent.py``).
"""

from __future__ import annotations

from dataclasses import replace

from ..config.settings import (
    GitHubAgentSettings,
    RagSettings,
    SlackAgentSettings,
    WorkspaceAgentSettings,
)
from ..llm import build_llm_provider
from ..rag import RagPipeline, build_rag_pipeline
from ..rag.prompts import SLACK_PROMPT_PROFILE, WORKSPACE_PROMPT_PROFILE
from .github_agent import GitHubAgent
from .policy_agent import PolicyAgent
from .slack_agent import SlackAgent
from .workspace_agent import WorkspaceAgent


def build_policy_agent(
    pipeline: RagPipeline | None = None, **pipeline_kwargs
) -> PolicyAgent:
    """Build a ``PolicyAgent``, defaulting its pipeline from configuration."""
    return PolicyAgent(pipeline or build_rag_pipeline(**pipeline_kwargs))


def build_workspace_agent(
    pipeline: RagPipeline | None = None, **pipeline_kwargs
) -> WorkspaceAgent:
    """Build a ``WorkspaceAgent``, defaulting its pipeline from configuration.

    Shares every other RAG knob (``top_k``, ``similarity_threshold``, etc.)
    with the policy pipeline via the same ``RagSettings.from_env()`` — only
    the fallback string and prompt framing differ, and web-search defaults to
    off. Any of these can still be overridden via ``pipeline_kwargs``, exactly
    like ``build_policy_agent``.
    """
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
    """Build a ``SlackAgent``, defaulting its pipeline from configuration.

    Same shape as ``build_workspace_agent``, with one addition that is the
    whole point of this agent: the pipeline is pinned to
    ``source_provider="slack"``, so retrieval reaches only chunks ingested from
    Slack (see ``slack_agent.py``). Web search defaults off for the same reason
    it does for a workspace — "what did we decide in #eng?" has no public-web
    answer, and offering one would invite a confidently wrong external result
    in place of an honest miss.
    """
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


def build_github_agent(
    llm=None,
    reader_builder=None,
    settings: GitHubAgentSettings | None = None,
) -> GitHubAgent:
    """Build a ``GitHubAgent`` from configuration.

    Unlike ``build_policy_agent``/``build_workspace_agent`` there is no pipeline
    to construct — this agent has no retrieval (see ``github_agent.py``), so it
    needs only an LLM and a way to build a tenant-scoped reader.

    ``reader_builder`` defaults to ``githublive.build_github_reader``, imported
    lazily so constructing an agent doesn't drag in the DB/credentials layer at
    module-import time, and so tests can inject a fake without touching Postgres.
    It stays a *builder* because a reader is tenant-scoped and must be created
    per request from the ``org_id`` being served.
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
