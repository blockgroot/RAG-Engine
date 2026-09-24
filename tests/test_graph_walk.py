"""Second Brain 1.5: linking and the access-safe walk, on real Postgres.

The plan's "done when", each pinned directly:

* isolation holds both ways -- org, space, document;
* a path THROUGH a hidden node reveals nothing beyond it;
* unsharing hides dependent edges on the next walk, with no rebuild;
* a ``public_only`` viewer sees public evidence only;
* a walk is bounded and says when the bound stopped it.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.db.connection import get_connection
from app.graph import builder, identities
from app.graph.linking import link_question, question_identifiers
from app.graph.walk import walk
from app.sources.meta import build_meta, container, link, person
from app.vectorstore.base import Viewer

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs a database")

ADA = "ada@example.com"
BO = "bo@example.com"


@pytest.fixture
def pg():
    from app.db.migrate import apply_schema
    from app.vectorstore.pgvector_store import PgVectorStore

    apply_schema()
    return PgVectorStore()


@pytest.fixture
def org(pg):
    org_id = pg.create_organization(f"walk-{uuid.uuid4().hex[:8]}")
    yield org_id
    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))


def _vector():
    return [1.0] + [0.0] * (int(os.getenv("EMBEDDING_DIM", "1024")) - 1)


def _doc(pg, org_id, provider, external_id, title, meta=None, *, viewers=None, workspace_id=None):
    return pg.upsert_source_document(
        org_id, provider=provider, external_id=external_id, title=title, chunks=[title],
        embeddings=[_vector()], workspace_id=workspace_id, source_meta=meta or {},
        is_public=viewers is None, viewers=viewers,
    )


def _entity(org_id, key):
    with get_connection() as conn:
        return conn.execute(
            "SELECT id::text FROM kg_entities WHERE org_id = %s::uuid AND key = %s", (org_id, key)
        ).fetchone()[0]


def _names(result):
    return {e.name for e in result.entities}


def viewer(email):
    return Viewer(email=email)


@pytest.fixture
def chain(pg, org):
    """public -> secret -> public-behind-the-secret, and a public neighbour.

    ``Public plan`` references ``Secret memo`` (shared with Ada only), which
    references ``Beyond``. ``Public plan`` is also in #eng with ``Neighbour``.
    """
    _doc(pg, org, "google", "beyond", "Beyond")
    _doc(pg, org, "google", "secret", "Secret memo",
         build_meta(links=[link("google", "beyond")]), viewers=[ADA])
    _doc(pg, org, "google", "public", "Public plan",
         build_meta(links=[link("google", "secret")],
                    containers=[container("google", "folder", "F1", "Plans")]))
    _doc(pg, org, "google", "neighbour", "Neighbour",
         build_meta(containers=[container("google", "folder", "F1", "Plans")]))
    builder.build_documents(org, None, "google", ["beyond", "secret", "public", "neighbour"])
    return org


def test_a_hidden_document_is_not_entered_and_nothing_past_it_is_reached(chain):
    seed = _entity(chain, "google:public")
    result = walk(chain, None, [seed], viewer(BO))
    names = _names(result)
    assert "Secret memo" not in names
    assert "Beyond" not in names  # only reachable THROUGH the secret
    assert {"Public plan", "Neighbour", "Plans"} <= names


def test_someone_it_is_shared_with_walks_through_it(chain):
    result = walk(chain, None, [_entity(chain, "google:public")], viewer(ADA))
    assert {"Secret memo", "Beyond"} <= _names(result)


def test_evidence_documents_are_only_ones_the_viewer_may_open(chain):
    with get_connection() as conn:
        secret_id = conn.execute(
            "SELECT id::text FROM documents WHERE org_id = %s::uuid AND source_external_id = 'secret'",
            (chain,),
        ).fetchone()[0]
    for_bo = walk(chain, None, [_entity(chain, "google:public")], viewer(BO))
    for_ada = walk(chain, None, [_entity(chain, "google:public")], viewer(ADA))
    assert secret_id not in for_bo.document_ids
    assert secret_id in for_ada.document_ids


def test_a_hidden_document_is_not_even_a_seed(chain):
    secret = _entity(chain, "google:secret")
    assert walk(chain, None, [secret], viewer(BO)).entities == []
    assert [s.name for s in link_question(chain, None, "what does the Secret memo say", viewer(BO))] == []
    assert "Secret memo" in [
        s.name for s in link_question(chain, None, "what does the Secret memo say", viewer(ADA))
    ]


def test_unsharing_hides_it_on_the_next_walk_without_a_rebuild(pg, chain):
    seed = _entity(chain, "google:public")
    assert "Beyond" in _names(walk(chain, None, [seed], viewer(ADA)))
    pg.set_source_document_access(chain, provider="google", entries=[("secret", False, [BO])])
    after = _names(walk(chain, None, [seed], viewer(ADA)))
    assert "Secret memo" not in after and "Beyond" not in after


def test_public_only_sees_public_evidence_only(chain):
    result = walk(chain, None, [_entity(chain, "google:public")], Viewer.public_only_viewer())
    assert "Secret memo" not in _names(result)
    assert "Public plan" in _names(result)


def test_another_org_or_space_reaches_nothing(pg, chain):
    from app.auth.users import invite_member
    from app.workspaces import create_workspace

    seed = _entity(chain, "google:public")
    other_org = pg.create_organization(f"walk-other-{uuid.uuid4().hex[:8]}")
    try:
        assert walk(other_org, None, [seed], viewer(ADA)).entities == []
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (other_org,))

    owner = invite_member(f"o-{uuid.uuid4().hex[:6]}@example.com", chain)
    space = create_workspace(chain, "Space", owner.id)
    assert walk(chain, space, [seed], viewer(ADA)).entities == []
    assert link_question(chain, space, "Public plan", viewer(ADA)) == []


def test_the_walk_is_capped_and_says_so(pg, org):
    ids = [f"t{i}" for i in range(12)]
    for i in ids:
        _doc(pg, org, "slack", i, f"thread {i}",
             build_meta(containers=[container("slack", "channel", "C1", "general")]))
    builder.build_documents(org, None, "slack", ids)
    hub = _entity(org, "slack:channel:C1")
    result = walk(org, None, [hub], viewer(ADA), max_edges=5)
    assert result.truncated is True and result.edges == 5
    assert walk(org, None, [hub], viewer(ADA)).truncated is False


def test_one_members_identities_cost_no_hop(pg, org):
    """Ada wrote a Slack thread and reviewed a PR on GitHub: the PR is two hops
    from the thread even though three identity edges sit between them."""
    from datetime import datetime, timezone

    from app.auth.users import invite_member

    ada = invite_member(f"ada-{uuid.uuid4().hex[:6]}@example.com", org)
    _doc(pg, org, "slack", "C1:1.0", "#eng: auth",
         build_meta(people=[person("slack", role="author", external_id="U1", email=ada.email)]))
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO activity_facts (org_id, provider, kind, actor, actor_key, subject,
                                        occurred_at, external_id)
            VALUES (%s::uuid, 'github', 'pr_reviewed', 'ada-gh', 'github:ada-gh', 'acme/api',
                    %s, 'acme/api#14:ada-gh')
            """,
            (org, datetime.now(timezone.utc)),
        )
    builder.build_documents(org, None, "slack", ["C1:1.0"])
    builder.build_facts(org, None)
    identities.link_github(org, ada.id, "ada-gh", None)
    builder.rebuild_people(org)

    result = walk(org, None, [_entity(org, "slack:C1:1.0")], viewer(ada.email))
    assert "acme/api#14" in _names(result)


def test_question_identifiers():
    aliases, prs, words = question_identifiers("did api#14 fix ENG-142 or #9?")
    assert aliases == ["ENG-142"]
    assert prs == ["api#14", "#9"]
    assert "api" in words


def test_a_linear_id_in_the_question_links_exactly(pg, org):
    _doc(pg, org, "linear", "uuid-142", "ENG-142 - Token refresh fails",
         build_meta(people=[person("linear", role="assignee", external_id="p1", name="Priya")]))
    builder.build_documents(org, None, "linear", ["uuid-142"])
    [seed] = link_question(org, None, "who owns eng-142?", viewer(ADA))[:1]
    assert seed.exact and seed.name.startswith("ENG-142")
    fuzzy = link_question(org, None, "anything on the token refresh fails bug?", viewer(ADA))
    assert fuzzy and fuzzy[0].name.startswith("ENG-142")
