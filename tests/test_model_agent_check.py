"""A company's own model is tested for Agent mode when it is saved: Agent
mode is a tool loop, and a model that never calls a tool drafts nothing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import llm_model
from app.core.exceptions import LLMProviderError
from app.llm.base import ChatResult, ToolCall

PRESET = SimpleNamespace(id="openai", base_url="https://api.example.com/v1")


@pytest.mark.parametrize("reply, expected", [
    (ChatResult(text=None, tool_calls=[ToolCall("c1", "save_note", '{"text": "ready"}')]), "native"),
    (ChatResult(text='{"name": "save_note", "arguments": {"text": "ready"}}'), "text"),
    (ChatResult(text="Saved: ready"), "none"),
])
def test_how_a_model_calls_tools_is_found_when_it_is_saved(monkeypatch, reply, expected):
    monkeypatch.setattr(llm_model, "preset_extra_body", lambda _id: ({}, False))
    monkeypatch.setattr(llm_model.OpenAICompatProvider, "generate_with_tools", lambda self, *a, **k: reply)
    assert llm_model._agent_probe(PRESET, "m", "k") == expected


def test_a_test_that_fails_never_blocks_saving(monkeypatch):
    def down(self, *a, **k):
        raise LLMProviderError("timed out")

    monkeypatch.setattr(llm_model, "preset_extra_body", lambda _id: ({}, False))
    monkeypatch.setattr(llm_model.OpenAICompatProvider, "generate_with_tools", down)
    assert llm_model._agent_probe(PRESET, "m", "k") == "unknown"


def test_agent_mode_says_a_model_without_tools_cannot_run_it(monkeypatch):
    from app.api import actions
    from app.llm import org_model

    summary = {"model": "our-llm", "agent": "none"}
    monkeypatch.setattr(org_model, "get_org_model_summary", lambda org: summary)
    assert actions._cannot_call_tools("o", "our-llm")
    assert not actions._cannot_call_tools("o", "claude-sonnet")        # not the company's own
    summary["agent"] = None                                           # saved before the test: allowed
    assert not actions._cannot_call_tools("o", "our-llm")
