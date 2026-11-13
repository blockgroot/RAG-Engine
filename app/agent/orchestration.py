"""Agent routing/dispatch as a LangGraph graph.

The one place that runs the chosen agent; a new connector is one more
getter and node. Routing inside the graph is a plain function on
``requested_agent`` (the choice is made before, in ``choose_agent``).
LangGraph is only a declarative ``StateGraph``; ``langgraph.prebuilt`` is
deliberately unused.

Agents come from injected zero-arg getters, invoked only on the routed
path, so unused agents never load their models. Entry point:
``build_agent_graph``, called once by ``deps.get_agent_graph``.
"""

from __future__ import annotations

import warnings
from typing import Any, Callable, Iterator, TypedDict

# langgraph's import logs a serializer deprecation warning we cannot reach
# (no checkpointer is used); a pre-set filter is overridden by the import, so
# its warnings are caught and every other one re-raised.
with warnings.catch_warnings(record=True) as _caught:
    warnings.simplefilter("always")
    from langgraph.graph import END, START, StateGraph
for _w in _caught:
    if "`allowed_objects`" not in str(_w.message):
        warnings.warn_explicit(_w.message, _w.category, _w.filename, _w.lineno)

from ..vectorstore.base import Viewer
from .base import AgentResponse

# Keys a client may pass as ``requested_agent``; anything else falls to the
# default. A fixed tuple, so internal keys ("workspace") are never selectable.
INSIGHTS_KEY = "insights"
DIRECT_AGENT_KEYS = ("github", "slack", "linear", "notion", "google")

WORKSPACE_KEY = "workspace"
POLICY_KEY = "policy"


class AgentState(TypedDict, total=False):
    question: str
    org_id: str
    workspace_id: str | None
    conversation_id: str | None
    requested_agent: str | None
    stream: bool
    response: AgentResponse
    chunks: Iterator[str]
    chart_spec: dict | None
    chart_refusal: str | None
    user_id: str | None
    role: str | None
    # WHO is asking, from the signed session at the API edge, never looked up
    # in a node.
    viewer: "Viewer | None"


def route_agent_key(workspace_id: str | None, requested_agent: str | None) -> str:
    """Pure routing decision.

    A direct request always wins, even in a workspace (each agent resolves its
    own connection from ``(org_id, workspace_id)``, never org-wide). Else a
    workspace routes to ``WorkspaceAgent``, else ``PolicyAgent``.
    """
    if requested_agent in DIRECT_AGENT_KEYS or requested_agent == INSIGHTS_KEY:
        return requested_agent
    if workspace_id is not None:
        return WORKSPACE_KEY
    return POLICY_KEY


def _route(state: AgentState) -> str:
    return route_agent_key(state.get("workspace_id"), state.get("requested_agent"))


def _agent_node(getter: Callable[[], Any]) -> Callable[[AgentState], dict]:
    """Wrap one agent getter as a graph node; the getter runs only when this
    node is routed."""

    def run(state: AgentState) -> dict:
        agent = getter()
        if state.get("stream"):
            chunks, response = agent.answer_stream(
                state["question"],
                state["org_id"],
                conversation_id=state.get("conversation_id"),
                workspace_id=state.get("workspace_id"),
                viewer=state.get("viewer"),
            )
            return {"chunks": chunks, "response": response}
        response = agent.answer(
            state["question"],
            state["org_id"],
            conversation_id=state.get("conversation_id"),
            workspace_id=state.get("workspace_id"),
            viewer=state.get("viewer"),
        )
        return {"response": response}

    return run


def _insights_node(getter: Callable[[], Any]) -> Callable[[AgentState], dict]:
    """Insights is not RAG: it needs the already-validated spec, not a retrieve."""

    def run(state: AgentState) -> dict:
        agent = getter()
        kwargs = {
            "conversation_id": state.get("conversation_id"),
            "workspace_id": state.get("workspace_id"),
            "viewer": state.get("viewer"),
            "spec": state.get("chart_spec"),
            "refusal": state.get("chart_refusal"),
            "user_id": state.get("user_id"),
            "role": state.get("role"),
        }
        if state.get("stream"):
            chunks, response = agent.answer_stream(
                state["question"], state["org_id"], **kwargs
            )
            return {"chunks": chunks, "response": response}
        response = agent.answer(state["question"], state["org_id"], **kwargs)
        return {"response": response}

    return run


def build_agent_graph(getters: dict[str, Callable[[], Any]]):
    """Compile the routing graph from ``{key: zero-arg agent getter}``.

    ``getters`` must cover every key ``_route`` returns; a gap is a wiring bug
    and ``StateGraph`` raises at build time.
    """
    graph = StateGraph(AgentState)
    for key, getter in getters.items():
        node = _insights_node(getter) if key == INSIGHTS_KEY else _agent_node(getter)
        graph.add_node(key, node)
        graph.add_edge(key, END)
    graph.add_conditional_edges(START, _route, {key: key for key in getters})
    return graph.compile()
