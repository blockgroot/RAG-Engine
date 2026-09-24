"""Fill the knowledge graph from what sync already stored (Second Brain step 1.4).

Built ONLY from the database -- ``documents.source_meta`` (step 1.1),
``activity_facts`` and ``person_identities`` -- never by calling a provider, so
building costs no API call and no LLM call, and can be re-run at any time.

Per scope (org-wide, or one space). A walk never crosses scopes, so nothing is
ever linked across them either: a reference from a space document to an
org-wide one is simply not created.

What becomes what:

========================  ====================================  ===============
relation                  from → to                             source
========================  ====================================  ===============
``edited``/``authored``   person → document                     source_meta
``assigned_to``           issue → person                        Linear assignee
``commented``             person → document                     commenters/repliers
``mentions``              document → person / channel           @-mentions
``part_of``               document → folder/channel/team/page   containers
``references``            document → document / issue / PR      links
``opened`` ``merged``     person → PR                           activity_facts
``reviewed``              person → PR                           activity_facts
``committed_to``          person → repo                         activity_facts
``part_of``               PR → repo                             activity_facts
``same_person``           identity → member                     person_identities
========================  ====================================  ===============

Every edge carries EVIDENCE saying why it exists and therefore who may see it
(see ``kg_evidence`` in schema.sql). Building for a document first deletes that
document's evidence, re-derives it, then garbage-collects edges left with none
-- which is what makes a re-run idempotent and makes a removed mention or a
reassignment disappear. An ``assigned_to`` edge that loses its evidence is
CLOSED (``valid_to``) instead of deleted, so history survives reassignment.

Every public function is best-effort at its call site: a stale graph is a
stale graph, never a failed sync (the ``_record_insight_facts`` posture).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..db.connection import get_connection
from . import identities

logger = logging.getLogger(__name__)

#: A reference target that ingests AFTER the document pointing at it would be
#: missed until that document changed; building a batch therefore also
#: re-links documents that reference it -- at most this many per batch.
MAX_INCOMING_REBUILD = 200
#: Documents per backfill pass on the tick.
BACKFILL_BATCH = 200

#: Relations that keep history: losing their evidence CLOSES them.
_HISTORICAL = frozenset({"assigned_to"})

_ROLE_EDGES = {
    # role: (relation, person_is_source)
    "editor": ("edited", True),
    "author": ("authored", True),
    "creator": ("authored", True),
    "commenter": ("commented", True),
    "participant": ("commented", True),
    "assignee": ("assigned_to", False),
    "mentioned": ("mentions", False),
}

_FACT_EDGES = {
    "pr_opened": "opened",
    "pr_merged": "merged",
    "pr_reviewed": "reviewed",
}

_DOC_KIND = {"linear": "issue"}


# -- keys -----------------------------------------------------------------------


def document_key(provider: str, external_id: str) -> str:
    return f"{provider}:{external_id}"


def identity_key(provider: str, external_id: str) -> str:
    return f"identity:{provider}:{external_id}"


def container_key(container: dict) -> str:
    """Notion's parent PAGE is itself a document, so it keys like one."""
    if container.get("kind") == "page":
        return document_key(container["provider"], container["external_id"])
    return f"{container['provider']}:{container['kind']}:{container['external_id']}"


def link_key(link: dict) -> str:
    return f"{link['provider']}:{link['external_id']}"


def pr_key(repo: str, number: str) -> str:
    return f"github:pr:{repo.lower()}#{number}"


def repo_key(repo: str) -> str:
    return f"github:repo:{repo.lower()}"


def _linear_identifier(title: str | None) -> str | None:
    """``ENG-142`` from ``ENG-142 - Fix login`` (``linear._issue_title``)."""
    head = (title or "").split(" - ", 1)[0].strip()
    if "-" in head and head.split("-", 1)[1].isdigit():
        return head.upper()
    return None


# -- the SQL layer ----------------------------------------------------------------


def _scope(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"{prefix}workspace_id IS NOT DISTINCT FROM %s::uuid"


def _conflict(workspace_id: str | None) -> str:
    if workspace_id is None:
        return "ON CONFLICT (org_id, key) WHERE workspace_id IS NULL"
    return "ON CONFLICT (org_id, workspace_id, key) WHERE workspace_id IS NOT NULL"


@dataclass
class _Batch:
    """Entities and edges for one build, written in three statements."""

    org_id: str
    workspace_id: str | None
    entities: dict[str, tuple[str, str, list[str] | None, str | None]] = field(default_factory=dict)
    # (src_key, dst_key, relation, evidence) ; evidence = (document_id, fact_id, is_public)
    edges: list[tuple[str, str, str, tuple[str | None, str | None, bool | None]]] = field(
        default_factory=list
    )

    def entity(self, key: str, kind: str, name: str, aliases=None, document_id=None) -> str:
        existing = self.entities.get(key)
        # A real name or document id beats a placeholder from an earlier mention.
        if existing is None or (document_id and not existing[3]):
            self.entities[key] = (kind, (name or key)[:500], aliases, document_id)
        return key

    def edge(self, src: str, dst: str, relation: str, *, document_id=None, fact_id=None, is_public=None):
        if src != dst:
            self.edges.append((src, dst, relation, (document_id, fact_id, is_public)))


def _write(conn, batch: _Batch, known_keys: dict[str, str]) -> None:
    """Upsert entities, then edges, then evidence. ``known_keys`` maps key → id
    for targets resolved in the database rather than created here."""
    ids = dict(known_keys)
    if batch.entities:
        rows = conn.execute(
            f"""
            INSERT INTO kg_entities (org_id, workspace_id, kind, key, name, aliases, document_id)
            SELECT %s::uuid, %s::uuid, e.kind, e.key, e.name,
                   string_to_array(e.aliases_csv, chr(31)), e.document_id::uuid
              FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[])
                   AS e(kind, key, name, aliases_csv, document_id)
            {_conflict(batch.workspace_id)}
            DO UPDATE SET
                name        = CASE WHEN EXCLUDED.document_id IS NOT NULL OR kg_entities.name = kg_entities.key
                                   THEN EXCLUDED.name ELSE kg_entities.name END,
                kind        = CASE WHEN EXCLUDED.document_id IS NOT NULL THEN EXCLUDED.kind ELSE kg_entities.kind END,
                aliases     = COALESCE(EXCLUDED.aliases, kg_entities.aliases),
                document_id = COALESCE(EXCLUDED.document_id, kg_entities.document_id)
            RETURNING key, id::text
            """,
            (
                batch.org_id,
                batch.workspace_id,
                [v[0] for v in batch.entities.values()],
                list(batch.entities),
                [v[1] for v in batch.entities.values()],
                [chr(31).join(v[2]) if v[2] else None for v in batch.entities.values()],
                [v[3] for v in batch.entities.values()],
            ),
        ).fetchall()
        ids.update(dict(rows))

    edge_rows = [
        (ids[src], ids[dst], relation, evidence)
        for src, dst, relation, evidence in batch.edges
        if src in ids and dst in ids
    ]
    if not edge_rows:
        return
    unique_edges = list(dict.fromkeys((s, d, r) for s, d, r, _ in edge_rows))
    edge_ids = dict(
        (tuple(row[:3]), row[3])
        for row in conn.execute(
            """
            INSERT INTO kg_edges (org_id, workspace_id, src_id, dst_id, relation)
            SELECT %s::uuid, %s::uuid, s::uuid, d::uuid, r
              FROM unnest(%s::text[], %s::text[], %s::text[]) AS t(s, d, r)
            ON CONFLICT (src_id, dst_id, relation) WHERE valid_to IS NULL
            DO UPDATE SET last_seen = now()
            RETURNING src_id::text, dst_id::text, relation, id::text
            """,
            (
                batch.org_id,
                batch.workspace_id,
                [e[0] for e in unique_edges],
                [e[1] for e in unique_edges],
                [e[2] for e in unique_edges],
            ),
        ).fetchall()
    )
    evidence = list(
        dict.fromkeys(
            (edge_ids[(s, d, r)], ev[0], ev[1], ev[2])
            for s, d, r, ev in edge_rows
            if (s, d, r) in edge_ids
        )
    )
    conn.cursor().executemany(
        """
        INSERT INTO kg_evidence (edge_id, document_id, fact_id, is_public)
        SELECT %s::uuid, %s::uuid, %s::uuid, %s
         WHERE NOT EXISTS (
             SELECT 1 FROM kg_evidence
              WHERE edge_id = %s::uuid
                AND document_id IS NOT DISTINCT FROM %s::uuid
                AND fact_id IS NOT DISTINCT FROM %s::uuid
         )
        """,
        [(e, d, f, p, e, d, f) for e, d, f, p in evidence],
    )


def collect_garbage(org_id: str, workspace_id: str | None) -> None:
    """Drop edges and entities a deletion left behind (no rebuild needed)."""
    with get_connection() as conn:
        _collect_garbage(conn, org_id, workspace_id)


def _collect_garbage(conn, org_id: str, workspace_id: str | None) -> None:
    """Close or delete edges with no evidence; drop entities nothing points at."""
    conn.execute(
        f"""
        UPDATE kg_edges e SET valid_to = now()
         WHERE e.org_id = %s::uuid AND {_scope('e')}
           AND e.valid_to IS NULL AND e.relation = ANY(%s)
           AND NOT EXISTS (SELECT 1 FROM kg_evidence ev WHERE ev.edge_id = e.id)
        """,
        (org_id, workspace_id, list(_HISTORICAL)),
    )
    conn.execute(
        f"""
        DELETE FROM kg_edges e
         WHERE e.org_id = %s::uuid AND {_scope('e')}
           AND NOT EXISTS (SELECT 1 FROM kg_evidence ev WHERE ev.edge_id = e.id)
           AND NOT (e.relation = ANY(%s) AND e.valid_to IS NOT NULL
                    AND EXISTS (SELECT 1 FROM kg_entities x
                                 WHERE x.id IN (e.src_id, e.dst_id) AND x.document_id IS NOT NULL))
        """,
        (org_id, workspace_id, list(_HISTORICAL)),
    )
    conn.execute(
        f"""
        DELETE FROM kg_entities x
         WHERE x.org_id = %s::uuid AND {_scope('x')}
           AND x.document_id IS NULL
           AND NOT EXISTS (SELECT 1 FROM kg_edges e WHERE e.src_id = x.id OR e.dst_id = x.id)
        """,
        (org_id, workspace_id),
    )


# -- documents ---------------------------------------------------------------------


def _document_rows(conn, org_id, workspace_id, provider, external_ids):
    return conn.execute(
        f"""
        SELECT id::text, source_provider, source_external_id, title, source_meta
          FROM documents
         WHERE org_id = %s::uuid AND {_scope()}
           AND source_provider = %s AND source_external_id = ANY(%s)
        """,
        (org_id, workspace_id, provider, list(external_ids)),
    ).fetchall()


def _referencing_documents(conn, org_id, workspace_id, targets: list[dict]) -> list[tuple[str, str]]:
    """Documents in scope whose links point at any of ``targets``."""
    if not targets:
        return []
    pattern = [f"{t['provider']}\x1f{t['external_id']}" for t in targets]
    return conn.execute(
        f"""
        SELECT DISTINCT d.source_provider, d.source_external_id
          FROM documents d, jsonb_array_elements(COALESCE(d.source_meta->'links', '[]'::jsonb)) l
         WHERE d.org_id = %s::uuid AND {_scope('d')}
           AND (l->>'provider') || chr(31) || (l->>'external_id') = ANY(%s)
         LIMIT %s
        """,
        (org_id, workspace_id, pattern, MAX_INCOMING_REBUILD),
    ).fetchall()


def _resolve_targets(conn, org_id, workspace_id, keys: set[str], identifiers: set[str]) -> dict[str, str]:
    """Existing entities in THIS scope for link targets. Never creates one."""
    if not keys and not identifiers:
        return {}
    rows = conn.execute(
        f"""
        SELECT key, id::text, aliases FROM kg_entities
         WHERE org_id = %s::uuid AND {_scope()}
           AND (key = ANY(%s) OR aliases && %s::text[])
        """,
        (org_id, workspace_id, list(keys), list(identifiers)),
    ).fetchall()
    resolved: dict[str, str] = {}
    for key, entity_id, aliases in rows:
        resolved[key] = entity_id
        for alias in aliases or []:
            resolved.setdefault(f"linear:{alias}", entity_id)
    return resolved


def _add_document(batch: _Batch, row) -> tuple[str, list[dict]]:
    """Queue one document's entities and edges; returns (doc key, its links)."""
    document_id, provider, external_id, title, meta = row
    meta = meta or {}
    key = document_key(provider, external_id)
    identifier = _linear_identifier(title) if provider == "linear" else None
    batch.entity(
        key, _DOC_KIND.get(provider, "document"), title or external_id,
        aliases=[identifier] if identifier else None, document_id=document_id,
    )
    for person in meta.get("people") or []:
        ext = identities.identity_external_id(person)
        mapping = _ROLE_EDGES.get(person.get("role") or "")
        if not ext or mapping is None:
            continue
        pkey = batch.entity(
            identity_key(person["provider"], ext), "person",
            person.get("name") or person.get("email") or ext,
        )
        relation, person_is_source = mapping
        src, dst = (pkey, key) if person_is_source else (key, pkey)
        batch.edge(src, dst, relation, document_id=document_id)
    links = list(meta.get("links") or [])
    for container in meta.get("containers") or []:
        if container.get("kind") == "page":
            # A parent PAGE is a document: linked like a reference, only if it
            # exists in this scope, never created as a placeholder.
            links.append({**container, "relation": "part_of"})
            continue
        ckey = container_key(container)
        batch.entity(ckey, container["kind"], container.get("name") or container["external_id"])
        batch.edge(key, ckey, "part_of", document_id=document_id)
    return key, links


def build_documents(
    org_id: str, workspace_id: str | None, provider: str, external_ids: list[str]
) -> int:
    """(Re)build the graph for these documents. Returns documents built.

    Idempotent. Also re-links documents in scope that REFERENCE any of these
    (capped), so a target ingested after the page pointing at it is joined up
    now rather than whenever that page next changes.
    """
    external_ids = list(dict.fromkeys(e for e in external_ids if e))
    if not external_ids:
        return 0
    with get_connection() as conn:
        rows = _document_rows(conn, org_id, workspace_id, provider, external_ids)
        targets = [{"provider": provider, "external_id": e} for e in external_ids]
        for row in rows:
            identifier = _linear_identifier(row[3]) if provider == "linear" else None
            if identifier:
                targets.append({"provider": "linear", "external_id": identifier})
        incoming = [
            (p, e) for p, e in _referencing_documents(conn, org_id, workspace_id, targets)
            if not (p == provider and e in external_ids)
        ]
        by_provider: dict[str, list[str]] = {}
        for p, e in incoming:
            by_provider.setdefault(p, []).append(e)
        for p, ids_ in by_provider.items():
            rows += _document_rows(conn, org_id, workspace_id, p, ids_)

        doc_ids = [r[0] for r in rows]
        conn.execute("DELETE FROM kg_evidence WHERE document_id = ANY(%s::uuid[])", (doc_ids,))

        batch = _Batch(org_id, workspace_id)
        people: list[dict] = []
        pending_links: list[tuple[str, str, list[dict]]] = []
        for row in rows:
            key, links = _add_document(batch, row)
            people.extend((row[4] or {}).get("people") or [])
            pending_links.append((key, row[0], links))
        _write(conn, batch, {})

        link_keys = {link_key(l) for _, _, links in pending_links for l in links}
        identifiers = {l["external_id"] for _, _, links in pending_links for l in links if l["provider"] == "linear"}
        resolved = _resolve_targets(conn, org_id, workspace_id, link_keys, identifiers)
        link_batch = _Batch(org_id, workspace_id)
        own = dict(_resolve_targets(conn, org_id, workspace_id, {k for k, _, _ in pending_links}, set()))
        for key, document_id, links in pending_links:
            for link in links:
                target = link_key(link)
                if target not in resolved:
                    continue  # not in THIS scope: never linked across scopes
                relation = link.get("relation") or (
                    "mentions" if link["external_id"].startswith("channel:") else "references"
                )
                link_batch.edge(key, target, relation, document_id=document_id)
        _write(conn, link_batch, {**own, **resolved})
        _collect_garbage(conn, org_id, workspace_id)

    _record_identities(org_id, people)
    return len(rows)


# -- facts (GitHub) -----------------------------------------------------------------


def build_facts(org_id: str, workspace_id: str | None) -> int:
    """(Re)build pull-request, review and commit edges from ``activity_facts``.

    GitHub has no documents, so its whole graph comes from facts. Rebuilt for
    the scope each time (facts are already capped per repo). Scope-level
    evidence, the same visibility charts give these rows.
    """
    with get_connection() as conn:
        facts = conn.execute(
            f"""
            SELECT id::text, kind, actor, actor_key, subject, external_id
              FROM activity_facts
             WHERE org_id = %s::uuid AND {_scope()} AND provider = 'github'
               AND actor_key IS NOT NULL AND external_id IS NOT NULL
            """,
            (org_id, workspace_id),
        ).fetchall()
        conn.execute(
            f"""
            DELETE FROM kg_evidence ev USING activity_facts f
             WHERE ev.fact_id = f.id AND f.org_id = %s::uuid AND {_scope('f')}
               AND f.provider = 'github'
            """,
            (org_id, workspace_id),
        )
        batch = _Batch(org_id, workspace_id)
        people: list[dict] = []
        for fact_id, kind, actor, actor_key, repo, external_id in facts:
            login = actor_key.split(":", 1)[1]
            pkey = batch.entity(identity_key("github", login), "person", actor or login)
            people.append({"provider": "github", "key": actor_key, "name": actor})
            rkey = batch.entity(repo_key(repo), "repo", repo)
            if kind == "commit":
                batch.edge(pkey, rkey, "committed_to", fact_id=fact_id, is_public=True)
                continue
            relation = _FACT_EDGES.get(kind)
            if relation is None or "#" not in external_id:
                continue
            number = external_id.split("#", 1)[1].split(":", 1)[0]
            prk = batch.entity(pr_key(repo, number), "pr", f"{repo}#{number}")
            batch.edge(pkey, prk, relation, fact_id=fact_id, is_public=True)
            batch.edge(prk, rkey, "part_of", fact_id=fact_id, is_public=True)
        _write(conn, batch, {})
        _collect_garbage(conn, org_id, workspace_id)
    _record_identities(org_id, people)
    return len(facts)


# -- people -----------------------------------------------------------------------


def _record_identities(org_id: str, people: list[dict]) -> None:
    if not people:
        return
    try:
        identities.upsert_identities(org_id, people)
        identities.auto_link_by_email(org_id)
        rebuild_people(org_id)
    except Exception:  # noqa: BLE001 - see module docstring
        logger.warning("graph: could not record identities for org %s", org_id, exc_info=True)


def rebuild_people(org_id: str) -> int:
    """Re-derive every ``same_person`` edge in the org from ``person_identities``.

    Pure SQL and cheap, which is why identity edges hang off per-connector
    person entities: linking or unlinking an account rewrites these edges only,
    and "moving edges back" on unlink is just this edge disappearing.
    Returns links written.
    """
    with get_connection() as conn:
        conn.execute(
            """
            DELETE FROM kg_edges WHERE org_id = %s::uuid AND relation = 'same_person'
            """,
            (org_id,),
        )
        links = conn.execute(
            """
            SELECT x.workspace_id::text, x.key, pi.user_id::text,
                   COALESCE(pi.display_name, split_part(u.email, '@', 1))
              FROM person_identities pi
              JOIN users u ON u.id = pi.user_id AND u.org_id = pi.org_id
              JOIN kg_entities x
                ON x.org_id = pi.org_id
               AND x.key = 'identity:' || pi.provider || ':' || pi.external_id
             WHERE pi.org_id = %s::uuid AND pi.user_id IS NOT NULL
            """,
            (org_id,),
        ).fetchall()
        by_scope: dict[str | None, _Batch] = {}
        for workspace_id, ikey, user_id, name in links:
            batch = by_scope.setdefault(workspace_id, _Batch(org_id, workspace_id))
            ukey = batch.entity(f"user:{user_id}", "person", name or "member")
            batch.entity(ikey, "person", ikey)  # exists; placeholder name never wins
            batch.edge(ikey, ukey, "same_person", is_public=True)
        for batch in by_scope.values():
            _write(conn, batch, {})
        for workspace_id in {w for w, *_ in links} | {None}:
            _collect_garbage(conn, org_id, workspace_id)
    return len(links)


# -- lifecycle ----------------------------------------------------------------------


def purge_provider(org_id: str, workspace_id: str | None, provider: str) -> int:
    """Remove a disconnected provider's graph rows in one scope.

    Its documents are deleted by the disconnect itself, which cascades their
    evidence; this removes the entities keyed by the provider (containers,
    GitHub PRs and repos, which have no document) and anything left dangling.
    """
    prefixes = [f"{provider}:%"]
    with get_connection() as conn:
        if provider == "github":
            conn.execute(
                f"""
                DELETE FROM kg_evidence ev USING activity_facts f
                 WHERE ev.fact_id = f.id AND f.org_id = %s::uuid AND {_scope('f')}
                   AND f.provider = 'github'
                """,
                (org_id, workspace_id),
            )
        removed = conn.execute(
            f"""
            DELETE FROM kg_entities
             WHERE org_id = %s::uuid AND {_scope()} AND key LIKE ANY(%s)
            """,
            (org_id, workspace_id, prefixes),
        ).rowcount
        _collect_garbage(conn, org_id, workspace_id)
    return removed or 0


def backfill(limit: int = BACKFILL_BATCH) -> int:
    """Build documents that have captured metadata but no entity yet.

    For tenants whose documents predate the graph. Runs on the tick, bounded
    to ``limit`` documents per pass, oldest scope first; each pass shrinks the
    set, so it converges and then costs one indexed query.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT d.org_id::text, d.workspace_id::text, d.source_provider, d.source_external_id
              FROM documents d
             WHERE d.source_meta IS NOT NULL AND d.source_external_id IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM kg_entities x
                    WHERE x.org_id = d.org_id
                      AND x.workspace_id IS NOT DISTINCT FROM d.workspace_id
                      AND x.key = d.source_provider || ':' || d.source_external_id
               )
             ORDER BY d.org_id, d.workspace_id
             LIMIT %s
            """,
            (limit,),
        ).fetchall()
    grouped: dict[tuple, list[str]] = {}
    for org_id, workspace_id, provider, external_id in rows:
        grouped.setdefault((org_id, workspace_id, provider), []).append(external_id)
    built = 0
    for (org_id, workspace_id, provider), ids_ in grouped.items():
        try:
            built += build_documents(org_id, workspace_id, provider, ids_)
        except Exception:  # noqa: BLE001 - one scope must not stop the rest
            logger.warning("graph: backfill failed for org %s", org_id, exc_info=True)
    return built
