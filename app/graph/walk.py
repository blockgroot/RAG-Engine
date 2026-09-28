"""Walk the knowledge graph as one viewer (Second Brain step 1.5).

The rule that shapes everything here: **filter at every hop** (plan invariant
4). The access check runs INSIDE the recursive step, not on the result, because
a path that passes through something the viewer cannot see discloses a
relationship even if the hidden node itself is dropped afterwards.

An edge may be crossed only when at least one of its evidence rows is visible
to the viewer, using the SAME predicate retrieval uses
(``security/visibility.py``) -- a document's sharing is read live from
``documents``, so an unshared file hides its edges on the very next walk. A
DOCUMENT entity may be entered only when the viewer may open that document:
a reference from a document you can read to one you cannot must not reveal the
second one's title, or lead anywhere past it.

Bounded (CLAUDE.md §2): at most ``MAX_HOPS`` hops and ``MAX_EDGES`` edges, and
``truncated`` says when the cap is what stopped it. ``same_person`` edges (one
member's identities across tools) cost no hop, so "the PR Ada reviewed" is as
near to Ada's Slack identity as to her GitHub one.

Three round trips at most, whatever the graph's size: seeds, walk, evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..db.connection import get_connection
from ..security.visibility import evidence_predicate, visibility_predicate

MAX_HOPS = 2
MAX_EDGES = 50
#: A path longer than this is a loop through zero-cost identity edges.
_MAX_PATH = 6


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


def walk(
    org_id: str,
    workspace_id: str | None,
    seeds: list[str],
    viewer=None,
    *,
    max_edges: int = MAX_EDGES,
) -> WalkResult:
    """Everything within ``MAX_HOPS`` of ``seeds`` that this viewer may see."""
    if not seeds:
        return WalkResult()
    acl = _acl(viewer)
    edge_ok, edge_params = visible_edge_sql("e", acl)
    node_ok, node_params = openable_entity_sql("nx", acl)
    seed_ok, seed_params = openable_entity_sql("x", acl)

    # No DISTINCT/ORDER BY on the outer query: Postgres evaluates a recursive
    # CTE only as far as the parent fetches, so the LIMIT stops the walk itself
    # (breadth-first) instead of trimming a fully expanded one.
    limit = max_edges + len(seeds) + 1
    sql = f"""
        WITH RECURSIVE walk(entity_id, depth, edge_id, path) AS (
            SELECT x.id, 0, NULL::uuid, ARRAY[x.id]
              FROM kg_entities x
             WHERE x.org_id = %s::uuid
               AND x.workspace_id IS NOT DISTINCT FROM %s::uuid
               AND x.id = ANY(%s::uuid[])
               AND {seed_ok}
          UNION ALL
            SELECT nx.id,
                   w.depth + CASE WHEN e.relation = 'same_person' THEN 0 ELSE 1 END,
                   e.id,
                   w.path || nx.id
              FROM walk w
              JOIN kg_edges e
                ON (e.src_id = w.entity_id OR e.dst_id = w.entity_id)
               AND e.org_id = %s::uuid
               AND e.workspace_id IS NOT DISTINCT FROM %s::uuid
               AND e.valid_to IS NULL
              JOIN kg_entities nx
                ON nx.id = CASE WHEN e.src_id = w.entity_id THEN e.dst_id ELSE e.src_id END
             WHERE w.depth < %s
               AND cardinality(w.path) < %s
               AND NOT nx.id = ANY(w.path)
               AND {edge_ok}
               AND {node_ok}
        )
        SELECT w.entity_id::text, x.kind, x.name, w.depth, w.edge_id::text, x.key
          FROM walk w JOIN kg_entities x ON x.id = w.entity_id
         LIMIT %s
    """
    params = [
        org_id, workspace_id, list(seeds), *seed_params,
        org_id, workspace_id,
        MAX_HOPS, _MAX_PATH,
        *edge_params, *node_params,
        limit,
    ]
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        edge_ids = list(dict.fromkeys(r[4] for r in rows if r[4]))
        # Either more distinct edges than the cap, or the row LIMIT (which
        # counts repeat visits too) is what ended the walk.
        truncated = len(edge_ids) > max_edges or len(rows) >= limit
        edge_ids = edge_ids[:max_edges]
        kept = {r[0] for r in rows if r[4] is None or r[4] in edge_ids}
        providers = _evidence_documents(conn, org_id, edge_ids, list(kept), acl)
        links = _crossed_links(conn, edge_ids, rows)

    seen: dict[str, WalkedEntity] = {}
    for entity_id, kind, name, depth, edge_id, key in rows:
        if entity_id in kept and (entity_id not in seen or depth < seen[entity_id].depth):
            seen[entity_id] = WalkedEntity(entity_id, kind, name, depth, key or "")
    return WalkResult(
        entities=sorted(seen.values(), key=lambda e: (e.depth, e.name)),
        document_ids=list(providers),
        edges=len(edge_ids),
        truncated=truncated,
        document_providers=providers,
        links=links,
    )


def _crossed_links(conn, edge_ids, rows) -> list[WalkedLink]:
    """The kept edges, named. Same connection, one more round trip."""
    if not edge_ids:
        return []
    depth_of = {r[4]: r[3] for r in rows if r[4]}
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
        WalkedLink(r[1], r[2] or "", r[3], r[4], r[5] or "", depth_of.get(r[0], 0))
        for r in found
    ]
    return sorted(links, key=lambda l: (l.depth, l.relation, l.src_name, l.dst_name))


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
