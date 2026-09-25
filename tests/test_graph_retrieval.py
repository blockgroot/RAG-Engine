"""Second Brain 1.6: the knowledge graph as one more ranked retrieval list.

Pinned: off by default and then a strict no-op; on, it adds ONE vector search
restricted to the walk's evidence documents, with the viewer passed again; the
gate is still the best cosine; and any graph failure costs only its candidates.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.config.settings import GraphSettings, RagSettings, RetrievalSettings
from app.graph.linking import Seed
from app.graph.walk import WalkResult
from app.rag.retrieval import HybridRetriever
from app.vectorstore.base import RetrievedChunk, Viewer


def _chunk(doc, score, index=0):
    return RetrievedChunk(
        content=f"{doc} text", score=score, document_id=doc, chunk_index=index, org_id="o"
    )


class _Store:
    def __init__(self):
        self.calls: list[dict] = []

    def query(self, org_id, embedding, **kw):
        self.calls.append(kw)
        if kw.get("document_ids"):
            return [_chunk("graph-doc", 0.41)]
        return [_chunk("vector-doc", 0.52)]

    def keyword_search(self, *a, **kw):
        return []


def _retriever(store, *, enabled):
    return HybridRetriever(
        store,
        reranker=None,
        settings=RetrievalSettings(hybrid_enabled=False, rerank_enabled=False),
        rag_settings=RagSettings(top_k=5),
        graph_settings=GraphSettings(retrieval_enabled=enabled),
    )


@pytest.fixture
def graph_finds(monkeypatch):
    calls = {}

    def link(org_id, workspace_id, question, viewer):
        calls["link_viewer"] = viewer
        return [Seed("e1", "issue", "ENG-1 - Fix", True, 1.0)]

    def walk(org_id, workspace_id, seeds, viewer):
        calls["walk_viewer"] = viewer
        return WalkResult(document_ids=["graph-doc"], edges=2)

    monkeypatch.setattr("app.graph.linking.link_question", link)
    monkeypatch.setattr("app.graph.walk.walk", walk)
    return calls


def test_graph_retrieval_is_off_by_default(monkeypatch):
    monkeypatch.delenv("GRAPH_RETRIEVAL_ENABLED", raising=False)
    assert GraphSettings.from_env().retrieval_enabled is False


def test_off_means_no_graph_call_and_no_extra_search(graph_finds):
    store = _Store()
    result = _retriever(store, enabled=False).retrieve("o", "who owns ENG-1?", [1.0])
    assert graph_finds == {}
    assert all("document_ids" not in c for c in store.calls)
    assert result.graph_hits == 0


def test_on_adds_one_viewer_filtered_search_over_the_walks_documents(graph_finds):
    store = _Store()
    asker = Viewer(email="ada@example.com")
    result = _retriever(store, enabled=True).retrieve("o", "who owns ENG-1?", [1.0], viewer=asker)

    graph_calls = [c for c in store.calls if c.get("document_ids")]
    assert len(graph_calls) == 1
    assert graph_calls[0]["document_ids"] == ["graph-doc"]
    assert graph_calls[0]["viewer"] is asker  # filtered AGAIN, never trusted
    assert graph_finds["link_viewer"] is asker and graph_finds["walk_viewer"] is asker

    assert {h.document_id for h in result.hits} == {"vector-doc", "graph-doc"}
    assert result.gate_score == 0.52  # still the best cosine, never an RRF score
    assert result.graph_hits == 1


def test_a_graph_failure_costs_only_its_candidates(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("graph down")

    monkeypatch.setattr("app.graph.linking.link_question", boom)
    store = _Store()
    result = _retriever(store, enabled=True).retrieve("o", "who owns ENG-1?", [1.0])
    assert [h.document_id for h in result.hits] == ["vector-doc"]
    assert result.graph_hits == 0


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs a database")
def test_the_document_filter_still_applies_the_viewer():
    from app.db.connection import get_connection
    from app.db.migrate import apply_schema
    from app.vectorstore.pgvector_store import PgVectorStore

    apply_schema()
    pg = PgVectorStore()
    vector = [1.0] + [0.0] * (int(os.getenv("EMBEDDING_DIM", "1024")) - 1)
    org = pg.create_organization(f"graph-ret-{uuid.uuid4().hex[:8]}")
    try:
        public = pg.upsert_source_document(
            org, provider="google", external_id="pub", title="Pub",
            chunks=["a"], embeddings=[vector],
        )
        secret = pg.upsert_source_document(
            org, provider="google", external_id="sec", title="Sec",
            chunks=["b"], embeddings=[vector], is_public=False, viewers=["ada@example.com"],
        )
        pg.upsert_source_document(
            org, provider="google", external_id="other", title="Other",
            chunks=["c"], embeddings=[vector],
        )
        bo = Viewer(email="bo@example.com")
        hits = pg.query(org, vector, top_k=10, viewer=bo, document_ids=[public, secret])
        assert {h.document_id for h in hits} == {public}
        ada = pg.query(org, vector, top_k=10, viewer=Viewer(email="ada@example.com"),
                       document_ids=[public, secret])
        assert {h.document_id for h in ada} == {public, secret}
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org,))
