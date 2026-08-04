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


# --- Workspace-within-a-Workspace: workspace_id is a second isolation axis,
# nested INSIDE org_id (never queried alone). These prove a sub-workspace's
# content never leaks into the org-wide space, into a sibling workspace, or
# across an org boundary — and that org-wide behavior (workspace_id=None) is
# completely unaffected. See docs/plans/2026-08-03-workspace-within-workspace.md.


@requires_db
def test_workspace_chunks_invisible_to_org_wide_query(store, embedder, org_cleanup):
    from app.auth.users import create_admin
    from app.workspaces import create_workspace

    suffix = uuid.uuid4().hex[:8]
    org_id = store.create_organization(f"Workspace-Leak-Org-{suffix}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{suffix}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)

    secret = "The Q3 roadmap decision was to delay launch to October."
    _ingest_scoped(store, embedder, org_id, "Meeting Notes Doc", secret, workspace_id=workspace_id)

    query_vec = embedder.embed(["What was decided about the Q3 roadmap?"])[0]

    org_wide_hits = store.query(org_id=org_id, query_embedding=query_vec, top_k=5)
    assert all("Q3 roadmap decision" not in h.content for h in org_wide_hits), (
        "sub-workspace content leaked into the org-wide (workspace_id=None) query"
    )

    workspace_hits = store.query(
        org_id=org_id, query_embedding=query_vec, top_k=5, workspace_id=workspace_id
    )
    assert any("Q3 roadmap decision" in h.content for h in workspace_hits), (
        "the workspace's own content should be visible to a query scoped to it"
    )


@requires_db
def test_workspace_chunks_invisible_to_sibling_workspace(store, embedder, org_cleanup):
    from app.auth.users import create_admin
    from app.workspaces import create_workspace

    suffix = uuid.uuid4().hex[:8]
    org_id = store.create_organization(f"Workspace-Sibling-Org-{suffix}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{suffix}@example.com", org_id)
    workspace_a = create_workspace(org_id, "Workspace A", owner.id)
    workspace_b = create_workspace(org_id, "Workspace B", owner.id)

    secret = "The design review moved the release date to November 5th."
    _ingest_scoped(store, embedder, org_id, "Design Review Notes", secret, workspace_id=workspace_a)

    query_vec = embedder.embed(["When did the design review move the release date to?"])[0]

    hits_in_b = store.query(
        org_id=org_id, query_embedding=query_vec, top_k=5, workspace_id=workspace_b
    )
    assert all("design review moved the release date" not in h.content for h in hits_in_b), (
        "workspace A's content leaked into a query scoped to sibling workspace B"
    )

    hits_in_a = store.query(
        org_id=org_id, query_embedding=query_vec, top_k=5, workspace_id=workspace_a
    )
    assert any("design review moved the release date" in h.content for h in hits_in_a)


@requires_db
def test_org_wide_query_unaffected_by_workspace_scoping(store, embedder, org_cleanup):
    """Regression guard: existing org-wide (workspace_id=None) behavior is untouched."""
    suffix = uuid.uuid4().hex[:8]
    org_id = store.create_organization(f"Workspace-Regression-Org-{suffix}")
    org_cleanup.append(org_id)
    _ingest(store, embedder, org_id, "Leave Policy", ORG_CONTENT["Acme Corp"])

    query_vec = embedder.embed([QUESTION])[0]
    hits = store.query(org_id=org_id, query_embedding=query_vec, top_k=5)

    assert hits and DISTINCTIVE["Acme Corp"] in " ".join(h.content for h in hits)


@requires_db
def test_workspace_query_requires_matching_org_id(store, embedder, org_cleanup):
    """The right workspace_id with the WRONG org_id must return nothing — proves
    org_id is still load-bearing, never bypassed by workspace_id alone."""
    from app.auth.users import create_admin
    from app.workspaces import create_workspace

    suffix = uuid.uuid4().hex[:8]
    org_a = store.create_organization(f"Workspace-Org-A-{suffix}")
    org_b = store.create_organization(f"Workspace-Org-B-{suffix}")
    org_cleanup.extend([org_a, org_b])
    owner = create_admin(f"owner-{suffix}@example.com", org_a)
    workspace_id = create_workspace(org_a, "Meeting Notes", owner.id)

    secret = "The incident postmortem found a misconfigured load balancer."
    _ingest_scoped(store, embedder, org_a, "Postmortem", secret, workspace_id=workspace_id)

    query_vec = embedder.embed(["What did the incident postmortem find?"])[0]
    hits = store.query(org_id=org_b, query_embedding=query_vec, top_k=5, workspace_id=workspace_id)
    assert hits == []


def _ingest_scoped(
    store, embedder, org_id: str, title: str, raw_text: str, *, workspace_id: str
) -> None:
    chunks = chunk_text(preprocess(raw_text))
    embeddings = embedder.embed(chunks)
    store.add_document(
        org_id=org_id,
        title=title,
        chunks=chunks,
        embeddings=embeddings,
        workspace_id=workspace_id,
    )
