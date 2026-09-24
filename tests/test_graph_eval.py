"""Second Brain 1.7: the multi-hop eval that decides whether the graph goes on.

Two tiers, like ``test_retrieval_eval``:

* the MACHINERY, with a deterministic word-hashing embedder so it runs
  anywhere a database does: the corpus seeds, the graph builds, the ENG-142
  questions link, and the multi-hop documents reach the top results with the
  graph list on;
* the real measurement with the real embedder (the ``embedder`` fixture),
  asserting the rule the verdict encodes: ON never loses what OFF found.
"""

from __future__ import annotations

import hashlib
import math
import os
import uuid

import pytest

from app.db.connection import get_connection
from evaluation.graph_eval import (
    GRAPH_EVAL_CASES,
    GraphEvalResult,
    run_graph_suite,
    seed_graph_corpus,
    verdict,
)

needs_db = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs a database")


class _HashEmbedder:
    """Bag of words hashed into the configured dimension, L2-normalised."""

    def __init__(self):
        self.dim = int(os.getenv("EMBEDDING_DIM", "1024"))

    def embed(self, texts):
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for word in text.lower().split():
                vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def _case(found_off, found_on):
    return GraphEvalResult(GRAPH_EVAL_CASES[0], found_off, found_on, 0)


def test_verdict_needs_a_gain_and_no_loss():
    assert verdict([_case(False, True), _case(True, True)]).startswith("enable")
    assert verdict([_case(True, True)]).startswith("keep off: no gain")
    assert verdict([_case(False, True), _case(True, False)]).startswith("keep off: the graph lost")


@pytest.fixture
def graph_eval_org():
    from app.db.migrate import apply_schema
    from app.vectorstore.pgvector_store import PgVectorStore

    apply_schema()
    store = PgVectorStore()
    embedder = _HashEmbedder()
    org_id = seed_graph_corpus(store, embedder, f"GraphEvalTest-{uuid.uuid4().hex[:8]}")
    yield store, embedder, org_id
    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))
        conn.execute("DELETE FROM users WHERE email = %s", ("priya@graph-eval.example.com",))


@needs_db
def test_the_eval_machinery_links_walks_and_scores(graph_eval_org, monkeypatch):
    monkeypatch.setenv("RETRIEVAL_RERANK_ENABLED", "false")
    store, embedder, org_id = graph_eval_org
    results = {r.case.id: r for r in run_graph_suite(store, embedder, org_id)}

    for case_id in ("assignee-other-work", "discussion-of-issue"):
        assert results[case_id].graph_hits > 0, case_id
        assert results[case_id].found_on, case_id
    assert results["direct-lookup"].found_off and results["direct-lookup"].found_on


@needs_db
def test_with_the_real_embedder_the_graph_never_loses_a_case(embedder, monkeypatch):
    from app.vectorstore.pgvector_store import PgVectorStore

    monkeypatch.setenv("RETRIEVAL_RERANK_ENABLED", "false")
    store = PgVectorStore()
    org_id = seed_graph_corpus(store, embedder, f"GraphEvalReal-{uuid.uuid4().hex[:8]}")
    try:
        results = run_graph_suite(store, embedder, org_id)
        assert not [r.case.id for r in results if r.found_off and not r.found_on]
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))
            conn.execute("DELETE FROM users WHERE email = %s", ("priya@graph-eval.example.com",))
