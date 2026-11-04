"""Embeddings are stored and queried as ``halfvec``, and tenant searches scan iteratively.

``halfvec`` halves the table and cuts the HNSW index to a third of full
``vector`` (measured: 2,725 vs 8,380 bytes per vector). The query side has to be
halfvec too: a ``vector`` compared against a ``halfvec`` column cannot use the
index. ``hnsw.iterative_scan`` exists because every tenant shares one index:
without it a tenant's search returned 3.9 of 10 requested rows at 1.1M vectors.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import numpy as np
from pgvector import HalfVector

from app.agent import routing
from app.db.connection import get_connection
from app.vectorstore.pgvector_store import _to_db_vector

from .conftest import requires_db

ROOT = Path(__file__).resolve().parent.parent


def _unit(seed: int) -> list[float]:
    v = np.random.default_rng(seed).standard_normal(1024).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def test_query_vectors_bind_as_halfvec():
    assert isinstance(_to_db_vector(_unit(1)), HalfVector)


def test_schema_declares_no_full_precision_embedding_column():
    schema = (ROOT / "app/db/schema.sql").read_text()
    assert "embedding   halfvec(1024)" in schema
    assert "embedding    halfvec(1024)" in schema
    assert "hnsw (embedding halfvec_cosine_ops)" in schema
    assert not re.search(r"embedding\s+vector\(1024\)", schema)


def test_no_query_casts_an_embedding_to_full_vector():
    offenders = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in (ROOT / "app").rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if "embedding <=>" in line and "::vector" in line
    ]
    assert not offenders, offenders


@requires_db
def test_pooled_connections_scan_iteratively():
    with get_connection() as conn:
        assert conn.execute("SHOW hnsw.iterative_scan").fetchone()[0] == "strict_order"


class _FixedEmbedder:
    def __init__(self, vector: list[float]):
        self.vector = vector

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vector for _ in texts]


@requires_db
def test_store_and_probe_round_trip_on_halfvec(store, org_cleanup, monkeypatch):
    org_id = store.create_organization(f"halfvec-{uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    vectors = [_unit(i) for i in range(5)]
    store.add_document(
        org_id=org_id, title="Doc", chunks=[f"chunk {i}" for i in range(5)],
        embeddings=vectors,
    )
    with get_connection() as conn:
        kind = conn.execute(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
        ).fetchone()[0]
    assert kind == "halfvec(1024)"

    hits = store.query(org_id=org_id, query_embedding=vectors[3], top_k=5)
    assert hits[0].content == "chunk 3"
    assert hits[0].score > 0.999

    # The routing probe casts its parameter itself (`%s::halfvec`).
    monkeypatch.setattr(routing, "_probe_provider", _FixedEmbedder(vectors[2]))
    best = routing._probe_scope_best("anything", org_id, [None])
    assert best is not None and best[0] is None and best[1] > 0.999
