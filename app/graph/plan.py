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
    "member_of": "is a member of",
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
    #: Tools whose OWN searches join this answer, because the question named
    #: them ("...in Slack?"). Unlike ``cross`` this needs no graph evidence:
    #: the asker told us where to look. Viewer-filtered like every search.
    search: frozenset[str] = frozenset()
    #: Tools the question NAMED -> how many documents in that tool this viewer
    #: can open in this scope. Stated to the answer (``coverage_lines``) so a
    #: "no" can be said from evidence -- "Slack was searched, here is what it
    #: holds" -- rather than refused as if nothing had been looked at.
    coverage: tuple[tuple[str, int], ...] = ()

    @property
    def document_providers(self) -> dict[str, str]:
        return dict(getattr(self.walk, "document_providers", {}) or {})

    @property
    def providers(self) -> set[str]:
        """Every tool the walk reached a visible document in."""
        return {p for p in self.document_providers.values() if p}

    @property
    def seed_tools(self) -> set[str]:
        """Tools holding what the question REFERS to (its graph seeds)."""
        return {t for t in (provider_of(getattr(s, "key", "")) for s in self.seeds) if t}

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

        Grouped per ``(who, relation, tool)``: someone who wrote forty pages is
        ONE line naming how many were found and the best-matching few, not
        forty lines that crowd out everyone else. Links arrive best match to
        the question first (``walk``), so the few named are the relevant ones.
        """
        allowed = self.allowed(routed)
        groups: dict[tuple, list] = {}
        for link in getattr(self.walk, "links", []) or []:
            if link.relation == "same_person" and self.cross is None:
                continue
            touched = {p for p in (provider_of(link.src_key), provider_of(link.dst_key)) if p}
            if allowed is not None and touched and not touched <= allowed:
                continue
            key = (link.src_name, link.src_key, link.relation, provider_of(link.dst_key))
            groups.setdefault(key, []).append(link)
        out: list[str] = []
        size = 0
        full = False
        for links in groups.values():
            lines = ([_group_line(links)] if len(links) >= _GROUP_FROM
                     else [_fact_line(link) for link in links])
            for line in lines:
                if line in out:
                    continue
                if size + len(line) > MAX_FACTS_CHARS or len(out) >= MAX_FACTS:
                    full = True
                    break
                out.append(line)
                size += len(line)
            if full:
                break
        return out

    def coverage_lines(self) -> list[str]:
        """What was searched in each tool the question named, as plain facts."""
        return [
            f"{_TOOL_NAMES.get(tool, tool)} was searched for this question: "
            f"{count} item{'s' if count != 1 else ''} there are readable by the asker, "
            "and the closest matches are included in this context."
            for tool, count in self.coverage
        ]

    def connected(self, providers: set[str], *, search: set[str] = frozenset()) -> "GraphPlan":
        return replace(self, cross=frozenset(providers), search=frozenset(search))

    def search_tools(self, routed: str | None) -> list[str]:
        """Named tools to search besides the routed one, in a stable order."""
        return sorted(t for t in self.search if t != routed and t in _INDEXED)

    def other_providers(self, routed: str | None, usable: set[str]) -> set[str]:
        """Tools, besides the routed one, the graph found evidence in."""
        return {p for p in self.providers if p != routed and p in usable}


_TOOL_NAMES = {"google": "Google Drive", "notion": "Notion", "slack": "Slack",
               "linear": "Linear", "github": "GitHub"}
#: A person's links of one kind into one tool are grouped from this many on.
_GROUP_FROM = 3
#: Items named in one grouped fact, and how much of each title is kept.
_GROUP_SHOWN = 4
_TITLE_CHARS = 90


def _label(name: str, key: str) -> str:
    tool = provider_of(key)
    return f"{name} ({_TOOL_NAMES[tool]})" if tool in _TOOL_NAMES else name


def _short(title: str) -> str:
    title = " ".join((title or "").split())
    return title if len(title) <= _TITLE_CHARS else title[: _TITLE_CHARS - 1] + "…"


def _group_line(links) -> str:
    """``Sana (Slack) wrote 13 Slack items found, including: "a"; "b"; …``"""
    first = links[0]
    verb = _PHRASES.get(first.relation, first.relation.replace("_", " "))
    tool = _TOOL_NAMES.get(provider_of(first.dst_key) or "", "")
    shown = "; ".join(f'"{_short(l.dst_name)}"' for l in links[:_GROUP_SHOWN])
    more = f" and {len(links) - _GROUP_SHOWN} more" if len(links) > _GROUP_SHOWN else ""
    where = f" {tool}" if tool else ""
    return (f"{_label(first.src_name, first.src_key)} {verb} {len(links)}{where} "
            f"items found, including: {shown}{more}")


def _fact_line(link) -> str:
    verb = _PHRASES.get(link.relation, link.relation.replace("_", " "))
    return f"{_label(link.src_name, link.src_key)} {verb} {_label(link.dst_name, link.dst_key)}"


#: Tools that answer from an index, i.e. whose agents can take graph documents.
_INDEXED = {"notion", "google", "slack", "linear"}


def connected_tools(plan: GraphPlan | None, routed: str | None, named) -> set[str] | None:
    """The tools a PREDICTIVE connected answer should read, or ``None``.

    Fires when the question names one or more indexed tools other than the one
    routing picked -- "has the author of the Leave Policy discussed it in
    Slack?" routed to Notion, or "what has Sana done across Notion, Slack,
    Linear and Drive?". Each named tool's own searches then run alongside the
    routed one's (``GraphPlan.search``), with no graph evidence required: the
    graph proving a connection is one way to know another tool matters, the
    asker naming it is a stronger one. Only NAMED tools, never "all of them",
    so this is the corpora the asker pointed at, never the blended one.
    ``named`` is one tool, a set of them, or nothing.
    """
    if isinstance(named, str):
        named = {named}
    asked = {t for t in (named or ()) if t in _INDEXED}
    named = asked - {routed}
    if plan is None or routed not in _INDEXED or not named:
        return None
    # Several tools named ("the latest in Linear and Drive") is a list of
    # where to look: exactly those, nothing the asker did not mention.
    if len(asked) > 1:
        return asked | {routed}
    # One tool named, plus the tools holding what the question REFERS to:
    # "has the author of the Leave Policy discussed it in Slack?" routed to
    # Linear could read Sana's Slack thread but not that she wrote the Leave
    # Policy -- that fact lives in Notion -- and refused (measured with real
    # Gemini). Those tools join through the graph's documents and facts only;
    # their own search legs do not run (``search`` stays the named tools).
    return {routed} | named | (plan.seed_tools & _INDEXED)


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
        from ..agent.routing import named_provider
        from .linking import link_question
        from .walk import walk

        named = named_provider(question, _INDEXED)
        focus = frozenset({named}) if named else frozenset()
        seeds = link_question(org_id, workspace_id, question, viewer)
        result = (
            walk(org_id, workspace_id, [s.id for s in seeds], viewer,
                 question=question, focus=focus)
            if seeds else None
        )
        coverage = _coverage(org_id, workspace_id, focus, viewer)
        return GraphPlan(org_id, workspace_id, tuple(seeds), result, coverage=coverage)
    except Exception:  # noqa: BLE001 - the graph may only ever add; losing it costs nothing
        logger.warning("graph plan skipped", exc_info=True)
        return None


def _coverage(org_id, workspace_id, tools, viewer) -> tuple[tuple[str, int], ...]:
    """How many documents each NAMED tool holds for this viewer, in scope.

    One grouped COUNT, run only when the question named a tool. The same
    visibility predicate retrieval uses, so the number never counts a
    document the asker could not have been shown.
    """
    if not tools:
        return ()
    from ..db.connection import get_connection
    from ..security.visibility import visibility_predicate
    from .walk import _acl

    acl = _acl(viewer)
    doc_ok, params = ("TRUE", []) if acl is None else (visibility_predicate("d"), [acl])
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT d.source_provider, count(*)
              FROM documents d
             WHERE d.org_id = %s::uuid
               AND d.workspace_id IS NOT DISTINCT FROM %s::uuid
               AND d.source_provider = ANY(%s)
               AND {doc_ok}
             GROUP BY 1
            """,
            [org_id, workspace_id, sorted(tools), *params],
        ).fetchall()
    found = {r[0]: int(r[1]) for r in rows}
    return tuple((t, found.get(t, 0)) for t in sorted(tools))


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
