"""chunks: one partition per organization, and row-level security on top.

The database, not the query, keeps tenants apart here: a query that forgets its
org_id filter must return nothing, never another organization's text.
"""

from __future__ import annotations

import pytest

from app.db.connection import DatabaseError, get_connection
from tests.conftest import requires_db

pytestmark = requires_db

VECTOR = [1.0] + [0.0] * 1023


def _partitions(org: str) -> int:
    with get_connection() as conn:
        return conn.execute(
            "SELECT count(*) FROM pg_inherits WHERE inhparent = 'chunks'::regclass "
            "AND inhrelid::regclass::text = %s",
            ("chunk_partitions.org_" + org.replace("-", ""),),
        ).fetchone()[0]


@pytest.fixture
def two_orgs(store, org_cleanup):
    orgs = []
    for name in ("partition-a", "partition-b"):
        org = store.create_organization(name)
        org_cleanup.append(org)
        store.add_document(org, f"{name} doc", [f"{name} secret text"], [VECTOR])
        orgs.append(org)
    return orgs


def test_each_organization_gets_its_own_partition_and_loses_it_on_delete(store):
    org = store.create_organization("partition-lifecycle")
    try:
        assert _partitions(org) == 1
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s", (org,))
    assert _partitions(org) == 0


def test_no_organization_set_sees_no_chunks(two_orgs):
    with get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0


def test_a_query_without_org_filter_sees_only_its_own_organization(two_orgs):
    a, b = two_orgs
    with get_connection(org_id=a) as conn:
        rows = conn.execute("SELECT DISTINCT org_id::text, content FROM chunks").fetchall()
        asked_for_b = conn.execute(
            "SELECT count(*) FROM chunks WHERE org_id = %s", (b,)
        ).fetchone()[0]
    assert rows == [(a, "partition-a secret text")]
    assert asked_for_b == 0


def test_cannot_write_a_row_for_another_organization(two_orgs):
    a, b = two_orgs
    with pytest.raises(DatabaseError, match="row-level security"):
        with get_connection(org_id=a) as conn:
            conn.execute(
                "UPDATE chunks SET org_id = %s WHERE org_id = %s", (b, a)
            )


def test_search_through_the_store_still_finds_the_organizations_own_text(store, two_orgs):
    a, _ = two_orgs
    hits = store.query(a, VECTOR, top_k=5)
    assert [h.content for h in hits] == ["partition-a secret text"]
