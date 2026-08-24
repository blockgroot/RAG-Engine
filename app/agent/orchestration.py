"""Agent routing/dispatch as a LangGraph graph.

This is the ONE place that decides which agent answers a chat request and
runs it — it replaces the hand-rolled if/elif chain that used to live in
``app/api/chat.py``'s ``_select_agent``. As more connectors are added
(Confluence, GitHub Discussions, whatever comes next), extending this means
registering one more getter + one more node here, not growing an if/elif
chain across ``factory.py``/``deps.py``/``chat.py``.

Routing stays entirely deterministic — no LLM ever classifies a question to
pick an agent. That was a deliberate call made when GitHub was added (a
non-deterministic step in front of a tenant-scoped path is exactly what the
confidence gate's design philosophy avoids), and it does not change just
because the dispatch mechanism is now a graph: ``_route`` is a plain Python
function keyed on the caller-supplied ``requested_agent`` field, the same
signal ``_select_agent`` always used. LangGraph is used here purely as a
declarative execution graph (``langgraph.graph.StateGraph``), never its
LLM-driven tool-calling/ReAct machinery (``langgraph.prebuilt`` is
deliberately unused and unimported).

Agents are resolved via injected *getters* (zero-arg callables), not built
here — ``app/api/deps.py`` passes its ``lru_cache``d ``get_policy_agent`` /
``get_notion_agent`` / etc. so a node only constructs (and therefore loads
the embedder/reranker for) the agent actually on the routed path. Building
this module standalone with no getters wired up would be pointless — the
graph does nothing without them — so the only real entry point is
``build_agent_graph``, called once by ``deps.get_agent_graph``.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, TypedDict

from langgraph.graph import END, START, StateGraph

from .base import AgentResponse

# Keys a caller may pass as ``requested_agent`` — anything else (including
# None) falls through to the workspace/policy default in ``_route``. Kept as
# a tuple, not re-derived from the getters dict, so a caller can't silently
# make an internal-only key (e.g. "workspace") directly selectable from the
# client-facing ``agent`` field.
DIRECT_AGENT_KEYS = ("github", "slack", "linear", "notion", "google", "confluence")

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


def route_agent_key(workspace_id: str | None, requested_agent: str | None) -> str:
    """Pure routing decision — same precedence ``_select_agent`` always used.

    A direct request (github/slack/linear/notion/google) always wins, even
    inside a workspace — safety comes from each agent resolving its own
    connection from ``(org_id, workspace_id)``, never falling back to an
    org-wide one (see the agents' own docstrings). Otherwise a workspace_id
    routes to the generic ``WorkspaceAgent``, and with neither, the default
    is the legacy combined ``PolicyAgent`` (kept for back-compat callers that
    never adopted the per-source tabs — see chat.py's module docstring).
    """
    if requested_agent in DIRECT_AGENT_KEYS:
        return requested_agent
    if workspace_id is not None:
        return WORKSPACE_KEY
    return POLICY_KEY


def _route(state: AgentState) -> str:
    return route_agent_key(state.get("workspace_id"), state.get("requested_agent"))


def _agent_node(getter: Callable[[], Any]) -> Callable[[AgentState], dict]:
    """Wrap one agent getter as a graph node: call it, run the answer, done.

    The getter is invoked lazily, inside the node body — only when this node
    is actually the routed one — so building the graph never constructs an
    agent (and therefore never loads its embedder/reranker) up front.
    """

    def run(state: AgentState) -> dict:
        agent = getter()
        if state.get("stream"):
            chunks, response = agent.answer_stream(
                state["question"],
                state["org_id"],
                conversation_id=state.get("conversation_id"),
                workspace_id=state.get("workspace_id"),
            )
            return {"chunks": chunks, "response": response}
        response = agent.answer(
            state["question"],
            state["org_id"],
            conversation_id=state.get("conversation_id"),
            workspace_id=state.get("workspace_id"),
        )
        return {"response": response}

    return run


def build_agent_graph(getters: dict[str, Callable[[], Any]]):
    """Compile the routing graph from ``{key: zero-arg agent getter}``.

    ``getters`` must cover every key ``_route`` can return for the callers
    this graph serves — ``app/api/deps.get_agent_graph`` passes all of
    ``DIRECT_AGENT_KEYS`` plus ``"workspace"``/``"policy"``. A key ``_route``
    returns with no matching getter is a caller wiring bug, not a request
    error, so it is deliberately NOT guarded here — ``StateGraph`` raises
    immediately at ``add_conditional_edges`` if the mapping is incomplete.
    """
    graph = StateGraph(AgentState)
    for key, getter in getters.items():
        graph.add_node(key, _agent_node(getter))
        graph.add_edge(key, END)
    graph.add_conditional_edges(START, _route, {key: key for key in getters})
    return graph.compile()
