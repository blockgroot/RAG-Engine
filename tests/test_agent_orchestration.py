"""Tests for the LangGraph-based agent routing graph (app/agent/orchestration.py).

No real agents, no network, no models — each getter returns a fake agent
whose ``answer``/``answer_stream`` just records what it was called with, so
these tests pin the routing DECISION and the state plumbing, not any LLM
behavior.
"""

from __future__ import annotations

from app.agent.base import AgentResponse
from app.agent.orchestration import build_agent_graph, route_agent_key


class _FakeAgent:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def answer(self, question, org_id, *, conversation_id=None, workspace_id=None, **kwargs):
        self.calls.append(("answer", question, org_id, conversation_id, workspace_id, kwargs))
        return AgentResponse(answer=f"{self.name}:{question}", grounded=True, source=self.name)

    def answer_stream(self, question, org_id, *, conversation_id=None, workspace_id=None, **kwargs):
        self.calls.append(("stream", question, org_id, conversation_id, workspace_id, kwargs))
        response = AgentResponse(answer=f"{self.name}:{question}", grounded=True, source=self.name)
        return iter([response.answer]), response


def _graph_and_agents():
    keys = ("github", "slack", "linear", "notion", "google", "workspace", "policy", "insights")
    agents = {key: _FakeAgent(key) for key in keys}
    graph = build_agent_graph({key: (lambda a=a: a) for key, a in agents.items()})
    return graph, agents


def test_route_agent_key_direct_request_wins_over_workspace():
    assert route_agent_key("ws-1", "github") == "github"
    assert route_agent_key("ws-1", "notion") == "notion"
    assert route_agent_key(None, "linear") == "linear"


def test_route_agent_key_workspace_without_direct_request():
    assert route_agent_key("ws-1", None) == "workspace"
    assert route_agent_key("ws-1", "unrecognized") == "workspace"


def test_route_agent_key_defaults_to_policy():
    assert route_agent_key(None, None) == "policy"
    assert route_agent_key(None, "nonsense") == "policy"


def test_graph_dispatches_to_the_requested_agent():
    graph, agents = _graph_and_agents()
    state = graph.invoke(
        {"question": "q", "org_id": "org-1", "requested_agent": "notion", "stream": False}
    )
    assert state["response"].source == "notion"
    assert agents["notion"].calls == [("answer", "q", "org-1", None, None, {})]
    assert agents["google"].calls == []  # never touched — no cross-agent leakage


def test_graph_dispatches_to_workspace_agent_when_no_direct_request():
    graph, agents = _graph_and_agents()
    state = graph.invoke(
        {
            "question": "q",
            "org_id": "org-1",
            "workspace_id": "ws-1",
            "requested_agent": None,
            "stream": False,
        }
    )
    assert state["response"].source == "workspace"
    assert agents["workspace"].calls == [("answer", "q", "org-1", None, "ws-1", {})]


def test_graph_streaming_path_calls_answer_stream():
    graph, agents = _graph_and_agents()
    state = graph.invoke(
        {"question": "q", "org_id": "org-1", "requested_agent": "slack", "stream": True}
    )
    assert agents["slack"].calls[0][0] == "stream"
    assert "chunks" in state
    assert list(state["chunks"]) == ["slack:q"]


def test_only_the_routed_agent_is_ever_constructed():
    """Getters must be lazy: building the graph or invoking it must not touch
    an agent that wasn't routed to — the whole point of injecting getters
    instead of built agents (see the 16GB-Mac-hang gotcha in CLAUDE.md)."""
    built = []

    def make_getter(name):
        def getter():
            built.append(name)
            return _FakeAgent(name)
        return getter

    graph = build_agent_graph({k: make_getter(k) for k in ("policy", "notion", "google")})
    graph.invoke({"question": "q", "org_id": "org-1", "requested_agent": "notion", "stream": False})
    assert built == ["notion"]


def test_route_agent_key_insights_is_direct():
    assert route_agent_key("ws-1", "insights") == "insights"
    assert route_agent_key(None, "insights") == "insights"


def test_graph_dispatches_insights_with_the_validated_spec():
    graph, agents = _graph_and_agents()
    spec = {
        "metric": "issues_completed",
        "group_by": "subject",
        "period": "month",
        "chart": "pie",
    }
    state = graph.invoke(
        {
            "question": "q",
            "org_id": "org-1",
            "requested_agent": "insights",
            "stream": False,
            "chart_spec": spec,
            "user_id": "u1",
            "role": "admin",
        }
    )
    assert state["response"].source == "insights"
    kwargs = agents["insights"].calls[0][5]
    assert kwargs["spec"] == spec
    assert kwargs["user_id"] == "u1"
    assert kwargs["role"] == "admin"
    assert agents["linear"].calls == []
