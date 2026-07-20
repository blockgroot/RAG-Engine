"""Multi-tenant isolation test — the completion criterion for Phase 2.

Creates several fake organizations with *deliberately semantically similar*
content (same topic, different specifics) and proves that retrieval for one
organization never returns another organization's data — even when the query is
semantically close to every tenant's content.
"""

from __future__ import annotations

import uuid

from app.ingestion import chunk_text, preprocess
from .conftest import requires_db

# Same topic, near-identical phrasing — so a single query embeds close to ALL of
# them. Only the distinctive number distinguishes each tenant. This is what makes
# the test meaningful: similarity alone would happily cross tenants; the org_id
# filter must stop it.
ORG_CONTENT = {
    "Acme Corp": "Full-time employees receive 25 days of paid annual leave each year.",
    "Globex Inc": "Full-time employees receive 15 days of paid annual leave each year.",
    "Initech LLC": "Full-time employees receive 30 days of paid annual leave each year.",
}
DISTINCTIVE = {"Acme Corp": "25", "Globex Inc": "15", "Initech LLC": "30"}

QUESTION = "How many vacation days do staff get every year?"


def _ingest(store, embedder, org_id: str, title: str, raw_text: str) -> None:
    chunks = chunk_text(preprocess(raw_text))
    embeddings = embedder.embed(chunks)
    store.add_document(org_id=org_id, title=title, chunks=chunks, embeddings=embeddings)


@requires_db
def test_retrieval_is_isolated_per_org(store, embedder, org_cleanup):
    # Unique suffix so repeated runs never collide.
    suffix = uuid.uuid4().hex[:8]
    org_ids: dict[str, str] = {}
    for name, content in ORG_CONTENT.items():
        org_id = store.create_organization(f"{name}-{suffix}")
        org_cleanup.append(org_id)
        org_ids[name] = org_id
        _ingest(store, embedder, org_id, "Leave Policy", content)

    query_vec = embedder.embed([QUESTION])[0]

    for name, org_id in org_ids.items():
        hits = store.query(org_id=org_id, query_embedding=query_vec, top_k=5)

        # 1) We actually get results back for this tenant.
        assert hits, f"expected results for {name}"

        # 2) Every hit belongs to the tenant we queried — no leakage.
        assert all(h.org_id == org_id for h in hits), (
            f"{name} query returned another org's rows"
        )

        # 3) The tenant's own distinctive fact is present...
        joined = " ".join(h.content for h in hits)
        assert DISTINCTIVE[name] in joined, f"{name}'s own content missing"

        # 4) ...and NO other tenant's distinctive fact leaked in, despite the
        #    content being semantically almost identical.
        for other_name, number in DISTINCTIVE.items():
            if other_name == name:
                continue
            assert number not in joined, (
                f"{name} query leaked {other_name}'s content ({number} days)"
            )


@requires_db
def test_query_unknown_org_returns_nothing(store, embedder):
    """A tenant with no data (or an unknown org_id) gets zero rows, never a peek."""
    unknown_org = str(uuid.uuid4())
    query_vec = embedder.embed(["anything at all"])[0]
    hits = store.query(org_id=unknown_org, query_embedding=query_vec, top_k=5)
    assert hits == []
