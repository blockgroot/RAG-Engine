"""Agent mode sends each tool call back exactly as the provider returned it.

Gemini refuses the second turn of a tool loop ("Function call is missing a
thought_signature") when the loop rebuilds the call and drops the
``extra_content`` it attached. Any provider (a company can bring its own model)
may attach such a field, so the loop keeps every one, by name of none.
"""

from __future__ import annotations

from app.actions import service
from app.config.settings import ActionsSettings
from app.llm.base import ChatResult, LLMProvider, ToolCall

SIGNATURE = {"google": {"thought_signature": "EnMKcQFpFH0T"}}


class _TwoTurns(LLMProvider):
    def __init__(self) -> None:
        self.seen: list[list[dict]] = []

    def generate(self, prompt, *, max_tokens=None):
        raise AssertionError("the loop uses tool calls")

    def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
        self.seen.append(list(messages))
        if len(self.seen) == 1:
            return ChatResult(
                text=None,
                tool_calls=[ToolCall(id="call_1", name="no_such_tool", arguments="{}")],
                raw_message={"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "no_such_tool", "arguments": "{}"},
                    "extra_content": SIGNATURE, "unused": None,
                }]},
            )
        return ChatResult(text="Done.", tool_calls=[], raw_message={})


def test_provider_fields_on_a_tool_call_are_sent_back():
    llm = _TwoTurns()
    service._agent_loop(llm, "Do it.", [], [], org_id="o", user_id="u", email="e@x.com",
                        found=[], looked=set(), ran={}, settings=ActionsSettings.from_env())
    replayed = llm.seen[1][1]["tool_calls"][0]
    assert replayed["extra_content"] == SIGNATURE
    assert "unused" not in replayed                       # a null is not a field
    assert replayed["function"] == {"name": "no_such_tool", "arguments": "{}"}


def test_no_raw_message_still_replays_the_call():
    assert service._call_extras(None) == {}
    assert service._call_extras({"tool_calls": [{"id": "a", "type": "function"}]}) == {"a": {}}
