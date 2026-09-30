"""Second Brain 1.3/1.4: the graph tables and the builder, on real Postgres.

The plan's "done when" for 1.4, each pinned: running twice is idempotent;
deleting a document removes its edges; a cross-scope reference is never
created; disconnect purges. Plus reassignment closing (not deleting) an
``assigned_to`` edge, the late-target re-link, and linking/unlinking a member
rewriting only ``same_person`` edges.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from app.db.connection import get_connection
from app.graph import builder, identities
from app.sources.meta import build_meta, container, link, person

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs a database")


@pytest.fixture
def pg():
    from app.db.migrate import apply_schema
    from app.vectorstore.pgvector_store import PgVectorStore

    apply_schema()
    return PgVectorStore()


@pytest.fixture
def org(pg):
    org_id = pg.create_organization(f"graph-{uuid.uuid4().hex[:8]}")
    yield org_id
    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))


def _vector():
    dim = int(os.getenv("EMBEDDING_DIM", "1024"))
    return [1.0] + [0.0] * (dim - 1)


def _doc(pg, org_id, provider, external_id, title, meta, *, workspace_id=None, **kw):
    return pg.upsert_source_document(
        org_id, provider=provider, external_id=external_id, title=title,
        chunks=[title], embeddings=[_vector()], workspace_id=workspace_id,
        source_meta=meta, **kw,
    )


def _edges(org_id, *, current_only=True):
    clause = "AND e.valid_to IS NULL" if current_only else ""
    with get_connection() as conn:
        return sorted(
            conn.execute(
                f"""
                SELECT s.key, e.relation, d.key
                  FROM kg_edges e
                  JOIN kg_entities s ON s.id = e.src_id
                  JOIN kg_entities d ON d.id = e.dst_id
                 WHERE e.org_id = %s::uuid {clause}
                """,
                (org_id,),
            ).fetchall()
        )


def _entity_keys(org_id):
    with get_connection() as conn:
        return {
            r[0]
            for r in conn.execute(
                "SELECT key FROM kg_entities WHERE org_id = %s::uuid", (org_id,)
            ).fetchall()
        }


def _counts(org_id):
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT (SELECT count(*) FROM kg_entities WHERE org_id = %(o)s::uuid),
                   (SELECT count(*) FROM kg_edges WHERE org_id = %(o)s::uuid),
                   (SELECT count(*) FROM kg_evidence ev JOIN kg_edges e ON e.id = ev.edge_id
                     WHERE e.org_id = %(o)s::uuid)
            """,
            {"o": org_id},
        ).fetchone()


def _thread_meta():
    return build_meta(
        people=[
            person("slack", role="author", external_id="U1", name="Ada"),
            person("slack", role="participant", external_id="U2", name="Bo"),
            person("slack", role="mentioned", external_id="U3"),
        ],
        containers=[container("slack", "channel", "C1", "eng")],
    )


def test_a_document_becomes_people_edges_and_a_container(pg, org):
    _doc(pg, org, "slack", "C1:1.0", "#eng: deploy", _thread_meta())
    assert builder.build_documents(org, None, "slack", ["C1:1.0"]) == 1
    assert _edges(org) == [
        ("identity:slack:U1", "authored", "slack:C1:1.0"),
        ("identity:slack:U2", "commented", "slack:C1:1.0"),
        ("slack:C1:1.0", "mentions", "identity:slack:U3"),
        ("slack:C1:1.0", "part_of", "slack:channel:C1"),
    ]


def test_building_twice_is_idempotent(pg, org):
    _doc(pg, org, "slack", "C1:1.0", "#eng: deploy", _thread_meta())
    builder.build_documents(org, None, "slack", ["C1:1.0"])
    first = _counts(org)
    builder.build_documents(org, None, "slack", ["C1:1.0"])
    assert _counts(org) == first


def test_deleting_a_document_removes_its_edges(pg, org):
    _doc(pg, org, "slack", "C1:1.0", "#eng: deploy", _thread_meta())
    builder.build_documents(org, None, "slack", ["C1:1.0"])
    pg.delete_source_documents(org, "slack", ["C1:1.0"])
    builder.collect_garbage(org, None)
    assert _edges(org, current_only=False) == []
    assert _entity_keys(org) == set()


def test_a_reference_joins_documents_in_the_same_scope_even_when_the_target_comes_later(pg, org):
    source = build_meta(links=[link("notion", "page-b")])
    _doc(pg, org, "notion", "page-a", "A", source)
    builder.build_documents(org, None, "notion", ["page-a"])
    assert ("notion:page-a", "references", "notion:page-b") not in _edges(org)

    _doc(pg, org, "notion", "page-b", "B", {})
    builder.build_documents(org, None, "notion", ["page-b"])  # re-links A
    assert ("notion:page-a", "references", "notion:page-b") in _edges(org)


def test_a_reference_is_never_created_across_scopes(pg, org):
    from app.auth.users import invite_member
    from app.workspaces import create_workspace

    owner = invite_member(f"o-{uuid.uuid4().hex[:6]}@example.com", org)
    space = create_workspace(org, "Space", owner.id)
    _doc(pg, org, "notion", "org-page", "Org page", {})
    _doc(pg, org, "notion", "space-page", "Space page",
         build_meta(links=[link("notion", "org-page")]), workspace_id=space)
    builder.build_documents(org, None, "notion", ["org-page"])
    builder.build_documents(org, space, "notion", ["space-page"])
    assert not [e for e in _edges(org) if e[1] == "references"]


def test_a_linear_identifier_link_resolves_through_the_alias(pg, org):
    _doc(pg, org, "linear", "uuid-142", "ENG-142 - Fix login", {})
    _doc(pg, org, "google", "drive-doc", "Design",
         build_meta(links=[link("linear", "ENG-142")]))
    builder.build_documents(org, None, "linear", ["uuid-142"])
    builder.build_documents(org, None, "google", ["drive-doc"])
    assert ("google:drive-doc", "references", "linear:uuid-142") in _edges(org)


def test_reassignment_closes_the_old_edge_instead_of_deleting_it(pg, org):
    def issue(assignee):
        return build_meta(people=[person("linear", role="assignee", external_id=assignee)])

    _doc(pg, org, "linear", "i1", "ENG-1 - Fix", issue("ada"))
    builder.build_documents(org, None, "linear", ["i1"])
    _doc(pg, org, "linear", "i1", "ENG-1 - Fix", issue("bo"))
    builder.build_documents(org, None, "linear", ["i1"])

    assert [e for e in _edges(org) if e[1] == "assigned_to"] == [
        ("linear:i1", "assigned_to", "identity:linear:bo")
    ]
    history = [e for e in _edges(org, current_only=False) if e[1] == "assigned_to"]
    assert ("linear:i1", "assigned_to", "identity:linear:ada") in history


def test_github_facts_become_pr_and_repo_edges(pg, org):
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        conn.cursor().executemany(
            """
            INSERT INTO activity_facts (org_id, provider, kind, actor, actor_key, subject,
                                        occurred_at, external_id)
            VALUES (%s::uuid, 'github', %s, %s, %s, 'acme/api', %s, %s)
            """,
            [
                (org, "pr_opened", "Ada", "github:ada", now, "acme/api#7"),
                (org, "pr_reviewed", "bo", "github:bo", now, "acme/api#7:bo"),
                (org, "commit", "Sana Asiwal", None, now, "acme/api:abc"),  # no login: skipped
            ],
        )
    builder.build_facts(org, None)
    first = _counts(org)
    builder.build_facts(org, None)
    assert _counts(org) == first
    assert _edges(org) == [
        ("github:pr:acme/api#7", "part_of", "github:repo:acme/api"),
        ("identity:github:ada", "opened", "github:pr:acme/api#7"),
        ("identity:github:bo", "reviewed", "github:pr:acme/api#7"),
    ]


def test_linking_a_member_adds_same_person_and_unlinking_removes_it(pg, org):
    from app.auth.users import invite_member

    member = invite_member(f"ada-{uuid.uuid4().hex[:6]}@example.com", org)
    meta = build_meta(people=[person("slack", role="author", external_id="U1", email=member.email)])
    _doc(pg, org, "slack", "C1:1.0", "#eng: x", meta)
    builder.build_documents(org, None, "slack", ["C1:1.0"])  # records + auto-links the identity
    assert ("identity:slack:U1", "same_person", f"user:{member.id}") in _edges(org)

    gh = identities.link_github(org, member.id, "ada-gh", None)
    builder.rebuild_people(org)  # no github entity exists yet: nothing for it
    assert not [e for e in _edges(org) if e[0] == "identity:github:ada-gh"]

    identities.unlink(org, member.id, gh.id)
    with get_connection() as conn:
        conn.execute(
            "UPDATE person_identities SET user_id = NULL, verified_by = NULL "
            "WHERE org_id = %s::uuid AND provider = 'slack'",
            (org,),
        )
    builder.rebuild_people(org)
    assert not [e for e in _edges(org) if e[1] == "same_person"]
    assert f"user:{member.id}" not in _entity_keys(org)


def test_disconnect_purges_the_providers_graph(pg, org):
    _doc(pg, org, "slack", "C1:1.0", "#eng: deploy", _thread_meta())
    builder.build_documents(org, None, "slack", ["C1:1.0"])
    pg.delete_all_source_documents(org, "slack")
    builder.purge_provider(org, None, "slack")
    assert _entity_keys(org) == set()


def test_backfill_builds_documents_that_have_no_entity_yet(pg, org):
    _doc(pg, org, "slack", "C1:1.0", "#eng: deploy", _thread_meta())
    _doc(pg, org, "slack", "C1:2.0", "#eng: old", None)  # meta never captured: not a candidate
    builder.backfill()
    keys = _entity_keys(org)
    assert "slack:C1:1.0" in keys and "slack:C1:2.0" not in keys
