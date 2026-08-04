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

from ..config.settings import RagSettings, WorkspaceAgentSettings
from ..rag import RagPipeline, build_rag_pipeline
from ..rag.prompts import WORKSPACE_PROMPT_PROFILE
from .policy_agent import PolicyAgent
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
