"""Walk the knowledge graph as one viewer (Second Brain step 1.5).

The rule that shapes everything here: **filter at every hop** (plan invariant
4). The access check runs INSIDE each hop's query, not on the result, because
a path that passes through something the viewer cannot see discloses a
relationship even if the hidden node itself is dropped afterwards.

An edge may be crossed only when at least one of its evidence rows is visible
to the viewer, using the SAME predicate retrieval uses
(``security/visibility.py``) -- a document's sharing is read live from
``documents``, so an unshared file hides its edges on the very next walk. A
DOCUMENT entity may be entered only when the viewer may open that document:
a reference from a document you can read to one you cannot must not reveal the
second one's title, or lead anywhere past it.

**Ranked, fair, bounded -- because a real company's graph has hubs.** The first
version was one recursive CTE stopped by a LIMIT: breadth-first in whatever
order Postgres produced rows. That is fine on a toy corpus and wrong on a real
one, where one person authors hundreds of documents across four tools: the
first 50 rows were whichever tool's edges came out first, so "has she
discussed it in Slack?" could exhaust the budget on Notion before a single
Slack edge was read. Each hop is now its own query that

* expands ``same_person`` first (one member's identities across tools cost no
  hop, so "the PR Ada reviewed" is as near to her Slack identity as to her
  GitHub one);
* reads at most ``_POOL_PER_NODE`` newest candidate edges per frontier node
  (bounds the work on a hub before any scoring runs);
* scores each candidate against the QUESTION with Postgres full-text search --
  the neighbour's name, and for a document its indexed chunk text
  (``chunks.content_tsv``, already there for keyword retrieval) -- with
  recency as the tie-break. No model call and no embedding: the walk runs in
  parallel with routing and must not need one;
* then picks round-robin ACROSS TOOLS (tools the question named first), at
  most ``PER_NODE`` edges per frontier node, until the hop's share of
  ``MAX_EDGES`` is spent -- so no single tool and no single hub can crowd the
  others out.

``truncated`` says when a bound, not the graph, is what stopped the walk. Four
round trips whatever the graph's size: two hops, evidence, names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..db.connection import get_connection
from ..security.visibility import evidence_predicate, visibility_predicate

MAX_HOPS = 2
MAX_EDGES = 50
#: Edges one frontier node may contribute to a hop. A hub (someone who wrote
#: 400 documents) still gets its best ``PER_NODE`` in, never all of them.
PER_NODE = 20
#: Candidate edges read per frontier node before scoring, newest first. Bounds
#: the scoring work on a hub; the fair pick then chooses among these.
_POOL_PER_NODE = 300
#: Identity hops (``same_person``) within one hop's expansion: identity ->
#: member -> the member's other identities.
_IDENTITY_DEPTH = 3
_WORD = re.compile(r"[A-Za-z0-9]{3,}")


@dataclass(frozen=True)
class WalkedEntity:
    id: str
    kind: str
    name: str
    depth: int
    key: str = ""


@dataclass(frozen=True)
class WalkedLink:
    """One edge the walk crossed, by name -- the raw material of a readable fact.

    Both ends are entities the viewer was allowed to enter and the edge has
    evidence they may see, so naming them discloses nothing the walk itself
    did not already establish.
    """

    src_name: str
    src_key: str
    relation: str
    dst_name: str
    dst_key: str
    depth: int
    #: How well the edge's far end matched the question (0 when unscored).
    score: float = 0.0


@dataclass
class WalkResult:
    entities: list[WalkedEntity] = field(default_factory=list)
    #: Documents behind the visible evidence of every edge crossed, plus every
    #: document entity reached -- all of them openable by the viewer.
    document_ids: list[str] = field(default_factory=list)
    edges: int = 0
    truncated: bool = False
    #: document id -> ``documents.source_provider``, for every id above.
    document_providers: dict[str, str] = field(default_factory=dict)
    #: Every edge kept, by name, nearest first.
    links: list[WalkedLink] = field(default_factory=list)


def _acl(viewer) -> list[str] | None:
    """``None`` for an unrestricted viewer (no filter at all), else the ACL."""
    if viewer is None or viewer.is_unrestricted:
        return None
    return viewer.acl()


def visible_edge_sql(edge_alias: str, acl: list[str] | None) -> tuple[str, list]:
    """``EXISTS(...)`` true when the edge has evidence this viewer may see."""
    if acl is None:
        return f"EXISTS (SELECT 1 FROM kg_evidence ev WHERE ev.edge_id = {edge_alias}.id)", []
    return (
        f"""EXISTS (
            SELECT 1 FROM kg_evidence ev
              LEFT JOIN documents d ON d.id = ev.document_id
             WHERE ev.edge_id = {edge_alias}.id
               AND ((ev.document_id IS NOT NULL AND {visibility_predicate('d')})
                    OR (ev.document_id IS NULL AND {evidence_predicate('ev')})))""",
        [acl, acl],
    )


def openable_entity_sql(entity_alias: str, acl: list[str] | None) -> tuple[str, list]:
    """True when the entity is not a live document, or is one the viewer may open."""
    if acl is None:
        return "TRUE", []
    return (
        f"""({entity_alias}.document_id IS NULL OR EXISTS (
            SELECT 1 FROM documents d2
             WHERE d2.id = {entity_alias}.document_id AND {visibility_predicate('d2')}))""",
        [acl],
    )


def question_tsquery(question: str | None) -> str | None:
    """An OR full-text query over the question's words, or ``None``.

    OR, not AND: a relevant thread rarely repeats every word of the question.
    Words are alphanumeric only, so nothing the asker types can inject
    ``tsquery`` syntax; Postgres' own ``english`` configuration drops stop
    words and stems the rest -- no word list lives here.
    """
    words = list(dict.fromkeys(w.lower() for w in _WORD.findall(question or "")))
    return " | ".join(words[:32]) or None


def _tool_of(key: str) -> str:
    parts = (key or "").split(":")
    if parts[0] == "identity" and len(parts) > 1:
        return parts[1]
    return parts[0] if parts[0] and parts[0] != "user" else ""


def walk(
    org_id: str,
    workspace_id: str | None,
    seeds: list[str],
    viewer=None,
    *,
    max_edges: int = MAX_EDGES,
    question: str | None = None,
    focus: tuple[str, ...] | frozenset[str] = (),
    per_node: int = PER_NODE,
) -> WalkResult:
    """Everything within ``MAX_HOPS`` of ``seeds`` that this viewer may see,
    best-first for ``question`` and fair across tools (``focus`` first)."""
    if not seeds:
        return WalkResult()
    acl = _acl(viewer)
    tsq = question_tsquery(question)
    focus = frozenset(focus or ())

    reached: dict[str, WalkedEntity] = {}
    kept_edges: dict[str, tuple[int, float]] = {}  # edge id -> (depth, score)
    truncated = False
    frontier = list(dict.fromkeys(seeds))
    with get_connection() as conn:
        for depth in range(1, MAX_HOPS + 1):
            if not frontier or len(kept_edges) >= max_edges:
                break
            identities, candidates, pool_full = _hop(
                conn, org_id, workspace_id, frontier, list(reached) + frontier,
                acl, tsq, first=(depth == 1),
            )
            for ent_id, kind, name, key, edge_id in identities:
                if ent_id not in reached:
                    reached[ent_id] = WalkedEntity(ent_id, kind, name, depth - 1, key or "")
                if edge_id:
                    kept_edges.setdefault(edge_id, (depth - 1, 0.0))
            budget = max_edges - len(kept_edges)
            if depth < MAX_HOPS:
                budget = max(budget // 2, min(budget, per_node))
            picked, left_over = _fair_pick(candidates, budget, per_node, focus)
            truncated = truncated or pool_full or left_over
            nxt: list[str] = []
            for c in picked:
                kept_edges.setdefault(c["edge_id"], (depth, c["score"]))
                if c["nx_id"] not in reached:
                    reached[c["nx_id"]] = WalkedEntity(
                        c["nx_id"], c["kind"], c["name"], depth, c["key"] or ""
                    )
                    nxt.append(c["nx_id"])
            # The next hop starts from what this one reached; its own identity
            # closure (inside ``_hop``) adds each person's other tools for free.
            frontier = nxt

        edge_ids = list(kept_edges)[:max_edges]
        if len(kept_edges) > max_edges:
            truncated = True
        providers = _evidence_documents(conn, org_id, edge_ids, list(reached), acl)
        links = _crossed_links(conn, edge_ids, kept_edges)

    return WalkResult(
        entities=sorted(reached.values(), key=lambda e: (e.depth, e.name)),
        document_ids=list(providers),
        edges=len(edge_ids),
        truncated=truncated,
        document_providers=providers,
        links=links,
    )


def _hop(conn, org_id, workspace_id, frontier, visited, acl, tsq, *, first):
    """One hop: identity closure of the frontier, then its scored candidates.

    Returns ``(identities, candidates, pool_full)``. ``identities`` are
    ``same_person`` joins (free); on the first hop the seeds themselves come
    back too, with no edge, once they pass the openable check -- a document
    seed the viewer cannot open never starts a walk.
    """
    sp_edge_ok, sp_edge_params = visible_edge_sql("e", acl)
    sp_node_ok, sp_node_params = openable_entity_sql("nx", acl)
    seed_ok, seed_params = openable_entity_sql("x", acl)
    edge_ok, edge_params = visible_edge_sql("pe", acl)
    node_ok, node_params = openable_entity_sql("px", acl)
    sql = f"""
        WITH RECURSIVE sp(entity_id, edge_id, path) AS (
            SELECT x.id, NULL::uuid, ARRAY[x.id]
              FROM kg_entities x
             WHERE x.org_id = %s::uuid
               AND x.workspace_id IS NOT DISTINCT FROM %s::uuid
               AND x.id = ANY(%s::uuid[])
               AND {seed_ok}
          UNION ALL
            SELECT nx.id, e.id, sp.path || nx.id
              FROM sp
              JOIN kg_edges e
                ON (e.src_id = sp.entity_id OR e.dst_id = sp.entity_id)
               AND e.relation = 'same_person'
               AND e.org_id = %s::uuid
               AND e.workspace_id IS NOT DISTINCT FROM %s::uuid
               AND e.valid_to IS NULL
              JOIN kg_entities nx
                ON nx.id = CASE WHEN e.src_id = sp.entity_id THEN e.dst_id ELSE e.src_id END
             WHERE cardinality(sp.path) <= %s
               AND NOT nx.id = ANY(sp.path)
               AND {sp_edge_ok}
               AND {sp_node_ok}
        ),
        front AS (SELECT DISTINCT entity_id AS id FROM sp),
        cand AS (
            SELECT f.id AS from_id, e.id AS edge_id, e.relation, e.last_seen,
                   nx.id AS nx_id,
                   row_number() OVER (PARTITION BY f.id ORDER BY e.last_seen DESC) AS rn
              FROM front f
              JOIN kg_edges e
                ON (e.src_id = f.id OR e.dst_id = f.id)
               AND e.relation <> 'same_person'
               AND e.org_id = %s::uuid
               AND e.workspace_id IS NOT DISTINCT FROM %s::uuid
               AND e.valid_to IS NULL
              JOIN kg_entities nx
                ON nx.id = CASE WHEN e.src_id = f.id THEN e.dst_id ELSE e.src_id END
             WHERE NOT nx.id = ANY(%s::uuid[])
               AND nx.id NOT IN (SELECT id FROM front)
        ),
        q AS (SELECT CASE WHEN %s::text IS NULL THEN NULL
                          ELSE to_tsquery('english', %s::text) END AS tsq)
        SELECT 'id', s.entity_id::text, x.kind, x.name, x.key, s.edge_id::text,
               NULL::text, NULL::text, 0::float8, NULL::timestamptz, 0::bigint
          FROM sp s JOIN kg_entities x ON x.id = s.entity_id
         WHERE s.edge_id IS NOT NULL OR %s
        UNION ALL
        SELECT 'cand', c.nx_id::text, px.kind, px.name, px.key, c.edge_id::text,
               c.from_id::text, c.relation,
               COALESCE(ts_rank(to_tsvector('english', px.name), q.tsq), 0)
               + 2 * COALESCE((SELECT max(ts_rank(ch.content_tsv, q.tsq))
                                 FROM chunks ch WHERE ch.document_id = px.document_id), 0),
               c.last_seen, c.rn
          FROM cand c
          JOIN kg_edges pe ON pe.id = c.edge_id
          JOIN kg_entities px ON px.id = c.nx_id
          CROSS JOIN q
         WHERE c.rn <= %s
           AND {edge_ok}
           AND {node_ok}
    """
    params = [
        org_id, workspace_id, list(frontier), *seed_params,
        org_id, workspace_id, _IDENTITY_DEPTH, *sp_edge_params, *sp_node_params,
        org_id, workspace_id, list(visited),
        tsq, tsq,
        first,
        _POOL_PER_NODE, *edge_params, *node_params,
    ]
    rows = conn.execute(sql, params).fetchall()
    identities = [(r[1], r[2], r[3], r[4], r[5]) for r in rows if r[0] == "id"]
    candidates = [
        {"nx_id": r[1], "kind": r[2], "name": r[3], "key": r[4], "edge_id": r[5],
         "from_id": r[6], "relation": r[7], "score": float(r[8] or 0),
         "last_seen": r[9]}
        for r in rows if r[0] == "cand"
    ]
    pool_full = any(r[0] == "cand" and r[10] >= _POOL_PER_NODE for r in rows)
    return identities, candidates, pool_full


def _fair_pick(candidates, budget, per_node, focus):
    """Best-first, round-robin across tools, capped per frontier node.

    Returns ``(picked, left_over)``. Within a tool, candidates go best score
    first, newest breaking ties. Each round every tool gets one pick and a
    tool the question NAMED gets two, so naming Slack is honoured without
    Slack being the only thing walked. A ``per_node`` cap stops one hub from
    spending the whole budget. ``left_over`` is True when a pickable edge was
    left behind -- the walk was cut by a bound, which ``truncated`` reports.
    """
    if budget <= 0:
        return [], bool(candidates)
    best: dict[str, dict] = {}
    for c in candidates:  # one row per edge: keep its best-scoring approach
        cur = best.get(c["edge_id"])
        if cur is None or c["score"] > cur["score"]:
            best[c["edge_id"]] = c
    by_tool: dict[str, list[dict]] = {}
    for c in best.values():
        by_tool.setdefault(_tool_of(c["key"]), []).append(c)
    for group in by_tool.values():
        group.sort(key=lambda c: (c["score"], c["last_seen"] or 0), reverse=True)
    order = sorted(by_tool, key=lambda t: (t not in focus, -max(c["score"] for c in by_tool[t]), t))
    per_from: dict[str, int] = {}
    picked: list[dict] = []
    while len(picked) < budget and any(by_tool.values()):
        progressed = False
        for tool in order:
            for _ in range(2 if tool in focus else 1):
                group = by_tool[tool]
                while group:
                    c = group.pop(0)
                    if per_from.get(c["from_id"], 0) >= per_node:
                        continue
                    per_from[c["from_id"]] = per_from.get(c["from_id"], 0) + 1
                    picked.append(c)
                    progressed = True
                    break
                if len(picked) >= budget:
                    break
            if len(picked) >= budget:
                break
        if not progressed:
            break
    # Anything not picked was cut by a bound (the budget or the per-node cap),
    # never by the graph running out -- so it counts as truncation. A capped
    # hub that silently dropped 60 pages would read as a complete walk.
    picked_ids = {c["edge_id"] for c in picked}
    left_over = any(e not in picked_ids for e in best)
    return picked, left_over


def _crossed_links(conn, edge_ids, kept_edges) -> list[WalkedLink]:
    """The kept edges, named, best match to the question first."""
    if not edge_ids:
        return []
    found = conn.execute(
        """
        SELECT e.id::text, s.name, s.key, e.relation, d.name, d.key
          FROM kg_edges e
          JOIN kg_entities s ON s.id = e.src_id
          JOIN kg_entities d ON d.id = e.dst_id
         WHERE e.id = ANY(%s::uuid[])
        """,
        [edge_ids],
    ).fetchall()
    links = [
        WalkedLink(r[1], r[2] or "", r[3], r[4], r[5] or "",
                   kept_edges.get(r[0], (0, 0.0))[0], kept_edges.get(r[0], (0, 0.0))[1])
        for r in found
    ]
    return sorted(links, key=lambda l: (-l.score, l.depth, l.relation, l.src_name, l.dst_name))


def _evidence_documents(conn, org_id, edge_ids, entity_ids, acl) -> dict[str, str]:
    """Visible documents behind the crossed edges and the reached entities,
    mapped to the tool each came from."""
    if not edge_ids and not entity_ids:
        return {}
    if acl is None:
        doc_ok, doc_params = "TRUE", []
    else:
        doc_ok, doc_params = visibility_predicate("d"), [acl]
    rows = conn.execute(
        f"""
        SELECT DISTINCT d.id::text, d.source_provider
          FROM documents d
         WHERE d.org_id = %s::uuid
           AND (d.id IN (SELECT ev.document_id FROM kg_evidence ev
                          WHERE ev.edge_id = ANY(%s::uuid[]) AND ev.document_id IS NOT NULL)
                OR d.id IN (SELECT x.document_id FROM kg_entities x
                             WHERE x.id = ANY(%s::uuid[]) AND x.document_id IS NOT NULL))
           AND {doc_ok}
        """,
        [org_id, edge_ids, entity_ids, *doc_params],
    ).fetchall()
    return {r[0]: r[1] or "" for r in rows}
