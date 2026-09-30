"""Second Brain: ``member_of`` -- who is in a PRIVATE Slack channel.

Membership is sensitive on its own ("who is in #layoffs-planning"), so the
edge's evidence carries the channel's own ACL. Pinned: members of the channel
see it, nobody else does; a public channel gets no membership edges; a channel
that turns public, or a member who leaves, converges on the next build.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.db.connection import get_connection
from app.graph import builder
from app.graph.walk import walk
from app.sources.meta import build_meta, container, person
from app.vectorstore.base import Viewer

pytestmark = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs a database")

ADA, BO, CAROL = "ada@example.com", "bo@example.com", "carol@example.com"
CHANNEL = "slack:channel:C9"


@pytest.fixture
def pg():
    from app.db.migrate import apply_schema
    from app.vectorstore.pgvector_store import PgVectorStore

    apply_schema()
    return PgVectorStore()


@pytest.fixture
def org(pg):
    org_id = pg.create_organization(f"member-{uuid.uuid4().hex[:8]}")
    with get_connection() as conn:
        for ext, email, name in (("U1", ADA, "Ada"), ("U2", BO, "Bo"), ("U3", CAROL, "Carol")):
            conn.execute(
                "INSERT INTO person_identities (org_id, provider, external_id, email, display_name) "
                "VALUES (%s::uuid, 'slack', %s, %s, %s)",
                (org_id, ext, email, name),
            )
    yield org_id
    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))


def _thread(pg, org_id, ts, *, viewers, channel="C9", name="leadership"):
    return pg.upsert_source_document(
        org_id, provider="slack", external_id=f"{channel}:{ts}", title=f"#{name}: plan {ts}",
        chunks=["plan"], embeddings=[[1.0] + [0.0] * (int(os.getenv("EMBEDDING_DIM", "1024")) - 1)],
        tags=[f"slack:channel:{channel}"],
        source_meta=build_meta(
            people=[person("slack", role="author", external_id="U1", email=ADA, name="Ada")],
            containers=[container("slack", "channel", channel, name)],
        ),
        is_public=viewers is None, viewers=viewers,
    )


def _entity(org_id, key):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id::text FROM kg_entities WHERE org_id = %s::uuid AND key = %s", (org_id, key)
        ).fetchone()
    return row[0] if row else None


def _members(org_id, as_email):
    """People reachable from the channel over member_of, for this viewer."""
    result = walk(org_id, None, [_entity(org_id, CHANNEL)], Viewer(email=as_email))
    return {link.src_name for link in result.links if link.relation == "member_of"} | {
        link.dst_name for link in result.links if link.relation == "member_of"
    }


def _build(org_id, ts_list):
    builder.build_documents(org_id, None, "slack", [f"C9:{ts}" for ts in ts_list])
    return builder.build_memberships(org_id, None)


def test_members_see_the_membership_and_outsiders_do_not(pg, org):
    _thread(pg, org, "1.0", viewers=[ADA, BO, "channel:C9"])
    assert _build(org, ["1.0"]) == 2
    assert {"Ada", "Bo"} <= _members(org, ADA)
    assert _members(org, CAROL) == set()  # Carol cannot learn who is in the room


def test_a_public_channel_gets_no_membership_edges(pg, org):
    _thread(pg, org, "1.0", viewers=None)
    assert _build(org, ["1.0"]) == 0


def test_a_channel_that_turns_public_loses_its_membership(pg, org):
    _thread(pg, org, "1.0", viewers=[ADA, BO, "channel:C9"])
    _build(org, ["1.0"])
    _thread(pg, org, "1.0", viewers=None)
    assert _build(org, ["1.0"]) == 0
    assert _members(org, ADA) == set()


def test_a_member_who_leaves_drops_out(pg, org):
    _thread(pg, org, "1.0", viewers=[ADA, BO, "channel:C9"])
    _build(org, ["1.0"])
    _thread(pg, org, "1.0", viewers=[ADA, "channel:C9"])
    assert _build(org, ["1.0"]) == 1
    members = _members(org, ADA)
    assert "Ada" in members and "Bo" not in members


def test_an_email_with_no_slack_identity_is_skipped(pg, org):
    _thread(pg, org, "1.0", viewers=[ADA, "stranger@example.com", "channel:C9"])
    assert _build(org, ["1.0"]) == 1


def test_disconnecting_slack_purges_membership(pg, org):
    _thread(pg, org, "1.0", viewers=[ADA, BO, "channel:C9"])
    _build(org, ["1.0"])
    builder.purge_provider(org, None, "slack")
    with get_connection() as conn:
        left = conn.execute(
            "SELECT count(*) FROM kg_edges WHERE org_id = %s::uuid AND relation = 'member_of'", (org,)
        ).fetchone()[0]
    assert left == 0
