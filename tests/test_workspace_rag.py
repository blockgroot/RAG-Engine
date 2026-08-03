"""RagPipeline/PolicyAgent under workspace scoping (Task 6, Workspace-within-a-Workspace).

Proves the gate/strict-prompt/grounding logic is completely UNCHANGED when a
``workspace_id`` is supplied — only which chunks retrieval is allowed to see
changes. Uses the same real embeddings/pgvector/LLM fixtures as
tests/test_grounding.py (this is a grounding-correctness proof, not just an
isolation proof — see tests/test_isolation.py for the pure leak proofs).
"""

from __future__ import annotations

import uuid

from app.agent.policy_agent import PolicyAgent
from app.agent.workspace_agent import WorkspaceAgent
from app.ingestion import chunk_text, preprocess
from app.auth.users import create_admin
from app.workspaces import create_workspace

from .conftest import requires_db, requires_llm

MEETING_NOTES = """
# Q3 Planning Meeting Notes

## Roadmap Decision
The Q3 roadmap decision was to delay the public launch to October 15th, to
allow more time for load testing.

## Action Items
Priya owns the load-testing plan; Sam owns updating the external-facing docs.
"""


def _ingest_workspace(store, embedder, org_id: str, workspace_id: str, title: str, raw_text: str) -> None:
    chunks = chunk_text(preprocess(raw_text))
    embeddings = embedder.embed(chunks)
    store.add_document(
        org_id=org_id,
        title=title,
        chunks=chunks,
        embeddings=embeddings,
        workspace_id=workspace_id,
    )


@requires_db
@requires_llm
def test_workspace_scoped_question_is_grounded_in_workspace_content_only(
    rag, store, embedder, org_cleanup
):
    suffix = uuid.uuid4().hex[:8]
    org_id = store.create_organization(f"Workspace-RAG-Org-{suffix}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{suffix}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)
    _ingest_workspace(store, embedder, org_id, workspace_id, "Q3 Notes", MEETING_NOTES)

    result = rag.answer(
        "What was decided about the Q3 launch date?",
        org_id=org_id,
        workspace_id=workspace_id,
    )

    assert result.answered, f"expected a grounded answer, got fallback: {result.answer!r}"
    assert "October" in result.answer, result.answer
    assert result.sources, "expected grounding sources"
    joined = " ".join(s.content for s in result.sources)
    assert "October 15th" in joined


@requires_db
@requires_llm
def test_org_wide_question_falls_back_when_answer_only_lives_in_a_workspace(
    rag, store, embedder, org_cleanup
):
    """The same question, asked with NO workspace_id (org-wide), must fall back —
    a sub-workspace's content must never silently blend into the org-wide space."""
    suffix = uuid.uuid4().hex[:8]
    org_id = store.create_organization(f"Workspace-RAG-Leak-Org-{suffix}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{suffix}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)
    _ingest_workspace(store, embedder, org_id, workspace_id, "Q3 Notes", MEETING_NOTES)

    result = rag.answer("What was decided about the Q3 launch date?", org_id=org_id)

    assert not result.answered, f"expected fallback, got answer: {result.answer!r}"
    assert result.answer == rag._settings.fallback_response


@requires_db
@requires_llm
def test_sibling_workspace_question_falls_back(rag, store, embedder, org_cleanup):
    """A question asked in workspace B must not see workspace A's content."""
    suffix = uuid.uuid4().hex[:8]
    org_id = store.create_organization(f"Workspace-RAG-Sibling-Org-{suffix}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{suffix}@example.com", org_id)
    workspace_a = create_workspace(org_id, "Workspace A", owner.id)
    workspace_b = create_workspace(org_id, "Workspace B", owner.id)
    _ingest_workspace(store, embedder, org_id, workspace_a, "Q3 Notes", MEETING_NOTES)

    result = rag.answer(
        "What was decided about the Q3 launch date?",
        org_id=org_id,
        workspace_id=workspace_b,
    )

    assert not result.answered, f"expected fallback, got answer: {result.answer!r}"


@requires_db
@requires_llm
def test_policy_agent_passes_workspace_id_through(rag, store, embedder, org_cleanup):
    """PolicyAgent stays a thin adapter — workspace scoping flows through unchanged."""
    suffix = uuid.uuid4().hex[:8]
    org_id = store.create_organization(f"Workspace-Agent-Org-{suffix}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{suffix}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)
    _ingest_workspace(store, embedder, org_id, workspace_id, "Q3 Notes", MEETING_NOTES)

    agent = PolicyAgent(rag)
    response = agent.answer(
        "What was decided about the Q3 launch date?", org_id, workspace_id=workspace_id
    )

    assert response.grounded, f"expected a grounded answer, got fallback: {response.answer!r}"
    assert "October" in response.answer, response.answer


@requires_db
@requires_llm
def test_workspace_agent_answers_a_workspace_scoped_question_with_workspace_source(
    rag_workspace, store, embedder, org_cleanup
):
    """WorkspaceAgent (separate pipeline/prompt profile) answers a workspace
    question and labels it ``source="workspace"`` -- the split's whole point."""
    suffix = uuid.uuid4().hex[:8]
    org_id = store.create_organization(f"Workspace-Agent-Split-Org-{suffix}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{suffix}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)
    _ingest_workspace(store, embedder, org_id, workspace_id, "Q3 Notes", MEETING_NOTES)

    agent = WorkspaceAgent(rag_workspace)
    response = agent.answer(
        "What was decided about the Q3 launch date?", org_id, workspace_id=workspace_id
    )

    assert response.grounded, f"expected a grounded answer, got fallback: {response.answer!r}"
    assert response.source == "workspace"
    assert "October" in response.answer, response.answer
