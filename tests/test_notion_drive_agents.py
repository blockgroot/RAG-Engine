"""NotionAgent/DriveAgent: each pinned to its own source_provider, never mixed.

No network/DB/models: RagPipeline.__init__ is pure attribute assignment (it
never calls its llm/embedder/store/retriever during construction), so plain
placeholder objects are enough to prove build_notion_agent/build_drive_agent
wire the right source_provider and prompt profile into the pipeline.
"""

from __future__ import annotations

from app.agent import DriveAgent, NotionAgent, build_drive_agent, build_notion_agent
from app.agent.orchestration import route_agent_key
from app.rag.prompts import DRIVE_PROMPT_PROFILE, NOTION_PROMPT_PROFILE


def _pipeline_kwargs():
    return dict(
        llm=object(),
        embedder=object(),
        store=object(),
        retriever=object(),
        memory=None,
        web_search=None,
    )


def test_route_agent_key_covers_notion_and_google():
    assert route_agent_key(None, "notion") == "notion"
    assert route_agent_key(None, "google") == "google"
    # Direct request wins over workspace scoping, same as every other source.
    assert route_agent_key("ws-1", "notion") == "notion"
    assert route_agent_key("ws-1", "google") == "google"


def test_notion_and_drive_prompt_profiles_have_distinct_source_labels():
    assert NOTION_PROMPT_PROFILE.source_label == "notion"
    assert DRIVE_PROMPT_PROFILE.source_label == "google"
    assert NOTION_PROMPT_PROFILE.source_label != DRIVE_PROMPT_PROFILE.source_label


def test_build_notion_agent_pins_source_provider():
    agent = build_notion_agent(**_pipeline_kwargs())
    assert isinstance(agent, NotionAgent)
    assert agent.pipeline._source_provider == "notion"
    assert agent.pipeline._prompt_profile is NOTION_PROMPT_PROFILE


def test_build_drive_agent_pins_source_provider():
    agent = build_drive_agent(**_pipeline_kwargs())
    assert isinstance(agent, DriveAgent)
    assert agent.pipeline._source_provider == "google"
    assert agent.pipeline._prompt_profile is DRIVE_PROMPT_PROFILE
