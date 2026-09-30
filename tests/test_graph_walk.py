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


def test_links_and_document_tools_carry_nothing_the_walk_hid(chain):
    """The plan's facts and its per-tool documents come from these two fields."""
    seed = _entity(chain, "google:public")
    for_bo = walk(chain, None, [seed], viewer(BO))
    assert set(for_bo.document_providers) == set(for_bo.document_ids)
    assert set(for_bo.document_providers.values()) == {"google"}
    named = {n for link_ in for_bo.links for n in (link_.src_name, link_.dst_name)}
    assert "Secret memo" not in named and "Beyond" not in named
    assert for_bo.links and all(l.src_key and l.dst_key for l in for_bo.links)
    for_ada = walk(chain, None, [seed], viewer(ADA))
    assert "Secret memo" in {n for l in for_ada.links for n in (l.src_name, l.dst_name)}


def test_a_built_plan_states_no_fact_about_a_hidden_document(chain):
    from app.config.settings import GraphSettings
    from app.graph.plan import build_plan

    on = GraphSettings(retrieval_enabled=True)
    bo = build_plan(chain, None, "what is in the Public plan", viewer(BO), settings=on)
    ada = build_plan(chain, None, "what is in the Public plan", viewer(ADA), settings=on)
    assert bo is not None and bo.facts("google")
    assert not any("Secret memo" in f for f in bo.facts("google"))
    assert any("Secret memo" in f for f in ada.facts("google"))
    # Another tool's answer is told nothing from Drive.
    assert bo.facts("notion") == [] and bo.documents_for("notion") == []


# --------------------------------------------------------------------------
# Scale: one busy person across tools (the hub case the old LIMIT walk failed)
# --------------------------------------------------------------------------


@pytest.fixture
def hub(pg, org):
    """Sana wrote the Leave Policy, 80 other Notion pages and 5 Slack threads.

    One Notion page is about a remote-work stipend (in its TEXT, not its
    title); one Slack thread is about the scheduler.
    """
    from app.auth.users import invite_member

    sana = invite_member(f"sana-{uuid.uuid4().hex[:6]}@example.com", org)
    notion_me = person("notion", role="author", external_id="N1", email=sana.email, name="Sana")
    slack_me = person("slack", role="author", external_id="U1", email=sana.email, name="Sana")

    def doc(provider, ext, title, text):
        pg.upsert_source_document(
            org, provider=provider, external_id=ext, title=title, chunks=[text],
            embeddings=[_vector()], source_meta=build_meta(
                people=[notion_me if provider == "notion" else slack_me]),
        )

    doc("notion", "leave", "Leave Policy", "How annual leave accrues and carries over.")
    notion_ids = ["leave"]
    for i in range(80):
        text = ("Employees get a monthly remote work stipend for home office costs."
                if i == 57 else f"Procedure number {i} for the office.")
        doc("notion", f"p{i}", f"Handbook page {i}", text)
        notion_ids.append(f"p{i}")
    slack_ids = []
    for i in range(5):
        text = ("I added the scheduler flow yesterday for Slack, GitHub and Linear."
                if i == 3 else f"Standup notes {i}.")
        doc("slack", f"C1:{i}.0", f"#rag-updates: thread {i}", text)
        slack_ids.append(f"C1:{i}.0")
    builder.build_documents(org, None, "notion", notion_ids)
    builder.build_documents(org, None, "slack", slack_ids)
    return org


def _walked_titles(result):
    return {e.name for e in result.entities}


def test_a_busy_person_does_not_crowd_out_their_other_tools(hub):
    from app.graph.walk import MAX_EDGES, PER_NODE

    seed = _entity(hub, "notion:leave")
    result = walk(hub, None, [seed], viewer(ADA))
    tools = set(result.document_providers.values())
    assert tools == {"notion", "slack"}  # Slack reached despite 80 Notion pages
    assert result.edges <= MAX_EDGES and result.truncated
    notion_authored = [l for l in result.links
                       if l.relation == "authored" and l.dst_key.startswith("notion:")]
    assert len(notion_authored) <= PER_NODE + 1  # the hub is capped (+ the seed's own edge)


def test_the_walk_picks_what_the_question_is_about(hub):
    seed = _entity(hub, "notion:leave")
    result = walk(hub, None, [seed], viewer(ADA),
                  question="Who wrote about the remote work stipend?")
    assert "Handbook page 57" in _walked_titles(result)  # found by its TEXT, 1 of 80
    best = [l for l in result.links if l.dst_name == "Handbook page 57"]
    assert best and best[0].score > 0


def test_naming_a_tool_walks_that_tool_first(hub):
    seed = _entity(hub, "notion:leave")
    result = walk(hub, None, [seed], viewer(ADA), max_edges=12,
                  question="Has the author of the Leave Policy discussed the scheduler in Slack?",
                  focus={"slack"})
    assert "#rag-updates: thread 3" in _walked_titles(result)
    slack = [l for l in result.links if l.dst_key.startswith("slack:")]
    notion = [l for l in result.links if l.dst_key.startswith("notion:") and l.depth == 2]
    assert len(slack) >= len(notion)


def test_a_plan_groups_a_hubs_facts_and_states_coverage(hub):
    from app.config.settings import GraphSettings
    from app.graph.plan import build_plan

    plan = build_plan(hub, None, "Has the author of the Leave Policy discussed it in Slack?",
                      viewer(ADA), settings=GraphSettings(retrieval_enabled=True))
    assert plan.coverage == (("slack", 5),)
    connected = plan.connected({"notion", "slack"}, search={"slack"})
    facts = connected.facts("notion")
    grouped = [f for f in facts if "items found, including" in f]
    assert grouped, facts  # one line per (who, relation, tool), not 80
    assert len(facts) <= 12
    assert connected.coverage_lines() == [
        "Slack was searched for this question: 5 items there are readable by the asker, "
        "and the closest matches are included in this context."
    ]
    # A normal Notion answer still sees nothing of Slack.
    assert not any("Slack" in f for f in plan.facts("notion"))
