"""Human-readable policy/workspace citations (no raw document UUIDs)."""

from __future__ import annotations

from app.agent.rag_pipeline_agent import RagPipelineAgent
from app.vectorstore.base import RetrievedChunk


def test_citation_uses_document_title_not_uuid():
    chunk = RetrievedChunk(
        content="Employees get 25 days of leave.",
        score=0.8,
        document_id="c77b6204-6366-44b7-a622-a180f659634c",
        chunk_index=0,
        org_id="org",
        document_title="Architecture Review",
    )
    cite = RagPipelineAgent._to_citation(chunk)
    assert "Architecture Review" in cite.reference
    assert "excerpt 1" in cite.reference
    assert "c77b6204" not in cite.reference


def test_citation_strips_contextual_prefix():
    content = (
        "This chunk falls within the Architecture Review notes."
        + "\n\n"
        + "Attendees: Alice, Bob. Decision: migrate to CockroachDB."
    )
    chunk = RetrievedChunk(
        content=content,
        score=0.7,
        document_id="doc-1",
        chunk_index=2,
        org_id="org",
        document_title="Architecture Review",
    )
    cite = RagPipelineAgent._to_citation(chunk)
    assert "Attendees: Alice, Bob" in cite.content
    assert "This chunk falls within" not in cite.content
    assert "excerpt 3" in cite.reference


def test_citation_without_title_still_hides_uuid():
    chunk = RetrievedChunk(
        content="Some text",
        score=0.5,
        document_id="c77b6204-6366-44b7-a622-a180f659634c",
        chunk_index=1,
        org_id="org",
    )
    cite = RagPipelineAgent._to_citation(chunk)
    assert cite.reference == "Document excerpt 2"
    assert "c77b6204" not in cite.reference
