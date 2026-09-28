"""One graph walk per question, shared by routing, retrieval and the answer.

The graph is consulted three times for one question -- which tool should
answer (routing), which documents join retrieval, and which connections the
answer may state -- and each used to be its own walk or none. A ``GraphPlan``
is built ONCE, at the chat edge, in parallel with routing's own probe, and the
rest read it: the walk leaves the critical path instead of adding to it.

It also carries the one decision that lets the graph cross tools without
blending them: ``cross`` is False for a normal question, which keeps every
agent pinned to its own tool exactly as before; only an explicit connected
answer (``connected()``) widens the graph's documents and facts to the other
tools it reached. Normal search legs never widen -- only documents the graph
PROVED are connected, re-checked against the viewer at search time.

Never raises and never walks without a viewer: a routing or retrieval choice
informed by content the asker cannot read is a leak (CLAUDE.md, routing).
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, replace

from ..config.settings import GraphSettings

logger = logging.getLogger(__name__)

#: Facts handed to the answer, at most. A fact is one line naming two things
#: and how they relate; past a dozen they crowd out the documents themselves.
MAX_FACTS = 12
MAX_FACTS_CHARS = 1500

#: Relations phrased for a reader. Anything missing reads as its raw name.
_PHRASES = {
    "authored": "wrote",
    "edited": "last edited",
    "assigned_to": "is assigned to",
    "commented": "commented on",
    "mentions": "mentions",
    "part_of": "is in",
    "references": "links to",
    "opened": "opened",
    "merged": "merged",
    "reviewed": "reviewed",
    "committed_to": "committed to",
    "same_person": "is the same person as",
}
_KNOWN_PROVIDERS = {"notion", "google", "slack", "linear", "github"}


def provider_of(key: str) -> str | None:
    """The tool an entity key belongs to (``identity:slack:U1`` -> slack)."""
    if not key or key.startswith("user:"):
        return None
    parts = key.split(":")
    name = parts[1] if parts[0] == "identity" and len(parts) > 1 else parts[0]
    return name if name in _KNOWN_PROVIDERS else None


@dataclass(frozen=True)
class GraphPlan:
    org_id: str
    workspace_id: str | None
    seeds: tuple = ()
    walk: object | None = None  # graph.walk.WalkResult
    #: Tools this answer may draw graph documents and facts from. ``None`` =
    #: the routed tool only (normal); a set = a connected answer.
    cross: frozenset[str] | None = None

    @property
    def document_providers(self) -> dict[str, str]:
        return dict(getattr(self.walk, "document_providers", {}) or {})

    @property
    def providers(self) -> set[str]:
        """Every tool the walk reached a visible document in."""
        return {p for p in self.document_providers.values() if p}

    @property
    def exact_seeds(self) -> list:
        return [s for s in self.seeds if getattr(s, "exact", False)]

    def matches(self, org_id: str, workspace_id: str | None) -> bool:
        return self.org_id == org_id and self.workspace_id == workspace_id

    def allowed(self, routed: str | None) -> set[str] | None:
        """Tools graph documents may come from. ``None`` = no restriction."""
        if self.cross is not None:
            return set(self.cross)
        return {routed} if routed else None

    def documents_for(self, routed: str | None) -> list[str]:
        allowed = self.allowed(routed)
        return [
            doc for doc, prov in self.document_providers.items()
            if allowed is None or prov in allowed
        ]

    def facts(self, routed: str | None) -> list[str]:
        """Readable connections, limited to the tools this answer may use.

        A fact is kept when every tool it touches is allowed: a normal Notion
        answer states only Notion facts, so it never learns (or says) that a
        Slack thread exists. ``same_person`` joins are stated only in a
        connected answer, where crossing tools is the point.
        """
        allowed = self.allowed(routed)
        out: list[str] = []
        size = 0
        for link in getattr(self.walk, "links", []) or []:
            if link.relation == "same_person" and self.cross is None:
                continue
            touched = {p for p in (provider_of(link.src_key), provider_of(link.dst_key)) if p}
            if allowed is not None and touched and not touched <= allowed:
                continue
            line = _fact_line(link)
            if line in out:
                continue
            if size + len(line) > MAX_FACTS_CHARS or len(out) >= MAX_FACTS:
                break
            out.append(line)
            size += len(line)
        return out

    def connected(self, providers: set[str]) -> "GraphPlan":
        return replace(self, cross=frozenset(providers))

    def other_providers(self, routed: str | None, usable: set[str]) -> set[str]:
        """Tools, besides the routed one, the graph found evidence in."""
        return {p for p in self.providers if p != routed and p in usable}


def _label(name: str, key: str) -> str:
    tool = provider_of(key)
    names = {"google": "Google Drive", "notion": "Notion", "slack": "Slack",
             "linear": "Linear", "github": "GitHub"}
    return f"{name} ({names[tool]})" if tool in names else name


def _fact_line(link) -> str:
    verb = _PHRASES.get(link.relation, link.relation.replace("_", " "))
    return f"{_label(link.src_name, link.src_key)} {verb} {_label(link.dst_name, link.dst_key)}"


#: Tools that answer from an index, i.e. whose agents can take graph documents.
_INDEXED = {"notion", "google", "slack", "linear"}


def connected_tools(plan: GraphPlan | None, routed: str | None, named: str | None) -> set[str] | None:
    """The tools a PREDICTIVE connected answer should read, or ``None``.

    Fires only when the question names a tool other than the one routing
    picked AND the graph found a document the asker can open there -- "has
    the author of the Leave Policy discussed it in Slack?" routed to Notion.
    Naming a tool with no graph evidence stays a normal answer: widening on a
    word alone would be the blended corpus the per-tool agents exist to avoid.
    """
    if plan is None or routed not in _INDEXED or not named or named == routed:
        return None
    if named not in plan.providers or named not in _INDEXED:
        return None
    return {routed, named}


def escalation_tools(plan: GraphPlan | None, routed: str | None) -> set[str] | None:
    """The tools a connected RETRY of a refusal should read, or ``None``.

    Only when the graph already proved the question connects to visible
    documents in other tools; otherwise a refusal stays a refusal and costs
    nothing more.
    """
    if plan is None or routed not in _INDEXED or plan.cross is not None:
        return None
    others = plan.other_providers(routed, _INDEXED)
    return ({routed} | others) if others else None


def build_plan(
    org_id: str, workspace_id: str | None, question: str, viewer, *, settings: GraphSettings | None = None
) -> GraphPlan | None:
    """Link the question and walk once, as this viewer. ``None`` when off."""
    settings = settings or GraphSettings.from_env()
    if not settings.retrieval_enabled or viewer is None:
        return None
    try:
        from .linking import link_question
        from .walk import walk

        seeds = link_question(org_id, workspace_id, question, viewer)
        result = walk(org_id, workspace_id, [s.id for s in seeds], viewer) if seeds else None
        return GraphPlan(org_id, workspace_id, tuple(seeds), result)
    except Exception:  # noqa: BLE001 - the graph may only ever add; losing it costs nothing
        logger.warning("graph plan skipped", exc_info=True)
        return None


_CURRENT: contextvars.ContextVar[GraphPlan | None] = contextvars.ContextVar(
    "graph_plan", default=None
)


def use_plan(plan: GraphPlan | None) -> contextvars.Token:
    """Make ``plan`` the one this request's retrieval and answer read."""
    return _CURRENT.set(plan)


def current_plan() -> GraphPlan | None:
    return _CURRENT.get()


def reset_plan(token: contextvars.Token) -> None:
    _CURRENT.reset(token)
