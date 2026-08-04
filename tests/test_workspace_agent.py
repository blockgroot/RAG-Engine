"""Workspace Agent split: a separate agent/persona for sub-workspace content.

Deterministic unit tests with fakes — no DB, real LLM, or embedding model
(same convention as test_tone_compliance.py / test_recovery.py). Proves:
(1) ``PromptProfile`` substitution produces distinct policy vs. workspace
wording from the SAME ``build_grounded_prompt`` template, (2) a
``RagPipeline`` built with ``WORKSPACE_PROMPT_PROFILE`` labels an answered
result ``source="workspace"`` (not ``"policy"``) and uses its own fallback
string, (3) ``WorkspaceAgent`` maps that through to ``AgentResponse`` exactly
like ``PolicyAgent`` does (same shared ``RagPipelineAgent`` base).
"""

from __future__ import annotations

from dataclasses import replace

from app.agent.policy_agent import PolicyAgent
from app.agent.workspace_agent import WorkspaceAgent
from app.config.settings import RagSettings, RecoverySettings, ReuseSettings
from app.rag.pipeline import RagPipeline
from app.rag.prompts import (
    POLICY_PROMPT_PROFILE,
    WORKSPACE_PROMPT_PROFILE,
    build_grounded_prompt,
)

from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

ORG = "org-workspace-agent"
POLICY_FALLBACK = "I don't have information on that in the available policy documents."
WORKSPACE_FALLBACK = "I don't have anything about that in this workspace's connected content."


def _pipeline(llm: RecordingLLM, store: TopicAwareVectorStore, *, profile, fallback: str) -> RagPipeline:
    return RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(top_k=3, similarity_threshold=0.35, fallback_response=fallback),
        memory=None,
        web_search=None,
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
        prompt_profile=profile,
    )


# -- PromptProfile substitution (pure prompt-string assertions) --------------


def test_policy_profile_is_the_default():
    prompt = build_grounded_prompt("What is the leave policy?", ["25 days."], POLICY_FALLBACK)
    assert "a company policy assistant" in prompt
    assert "company-specific" in prompt
    assert "workspace" not in prompt.lower()


def test_workspace_profile_produces_distinct_generic_wording():
    prompt = build_grounded_prompt(
        "What was decided about the launch date?",
        ["Launch delayed to October."],
        WORKSPACE_FALLBACK,
        profile=WORKSPACE_PROMPT_PROFILE,
    )
    assert "a company policy assistant" not in prompt
    assert "company-specific" not in prompt
    assert "workspace-specific" in prompt
    assert "this workspace" in prompt


# -- RagPipeline labels the answer with the profile's source_label ----------


def test_workspace_pipeline_labels_answered_result_as_workspace_source():
    llm = RecordingLLM(answer="The launch was delayed to October 15th.")
    store = TopicAwareVectorStore(ORG, [("doc-1", "The Q3 launch was delayed to October 15th.")])
    pipeline = _pipeline(llm, store, profile=WORKSPACE_PROMPT_PROFILE, fallback=WORKSPACE_FALLBACK)

    result = pipeline.answer("What was decided about the launch?", org_id=ORG)

    assert result.answered
    assert result.source == "workspace"


def test_policy_pipeline_still_labels_answered_result_as_policy_source():
    """Default profile (omitted) must be byte-for-byte the old behavior."""
    llm = RecordingLLM(answer="You get 25 days of annual leave.")
    store = TopicAwareVectorStore(ORG, [("doc-1", "Employees get 25 days of paid annual leave.")])
    pipeline = _pipeline(llm, store, profile=POLICY_PROMPT_PROFILE, fallback=POLICY_FALLBACK)

    result = pipeline.answer("How many leave days do I get?", org_id=ORG)

    assert result.answered
    assert result.source == "policy"


def test_gate_miss_uses_the_pipelines_own_fallback_string():
    llm = RecordingLLM(answer="irrelevant")
    # Question shares no KeywordEmbedder topic with the stored chunk -> cosine 0 -> gate miss.
    store = TopicAwareVectorStore(ORG, [("doc-1", "dental coverage details")])
    pipeline = _pipeline(llm, store, profile=WORKSPACE_PROMPT_PROFILE, fallback=WORKSPACE_FALLBACK)

    result = pipeline.answer("What time is the meeting tomorrow?", org_id=ORG)

    assert not result.answered
    assert result.answer == WORKSPACE_FALLBACK
    assert result.source == "none"


# -- WorkspaceAgent maps RagResult -> AgentResponse like PolicyAgent does ----


def test_workspace_agent_answer_mirrors_policy_agent_shape():
    llm = RecordingLLM(answer="The launch was delayed to October 15th.")
    store = TopicAwareVectorStore(ORG, [("doc-1", "The Q3 launch was delayed to October 15th.")])
    pipeline = _pipeline(llm, store, profile=WORKSPACE_PROMPT_PROFILE, fallback=WORKSPACE_FALLBACK)
    agent = WorkspaceAgent(pipeline)

    response = agent.answer("What was decided about the launch?", ORG)

    assert response.grounded
    assert response.source == "workspace"
    assert response.citations and response.citations[0].content


def test_workspace_and_policy_agents_share_the_same_base_class():
    from app.agent.rag_pipeline_agent import RagPipelineAgent

    llm = RecordingLLM(answer="x")
    store = TopicAwareVectorStore(ORG, [])
    pipeline = _pipeline(llm, store, profile=POLICY_PROMPT_PROFILE, fallback=POLICY_FALLBACK)

    assert isinstance(PolicyAgent(pipeline), RagPipelineAgent)
    assert isinstance(WorkspaceAgent(pipeline), RagPipelineAgent)
