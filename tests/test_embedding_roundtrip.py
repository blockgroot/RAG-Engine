"""Prove the numpy -> pgvector serialization path works end to end.

Inserts chunks with *known, hand-built* embeddings (no model involved, so the
model is not what's under test), queries them back with a known vector, and
checks the returned cosine-similarity scores are sane:

- an identical vector scores ~1.0
- an orthogonal vector scores ~0.0
- results come back ordered by similarity

If numpy arrays did not serialize correctly into pgvector's ``vector`` type, this
test would fail (wrong scores or an insert/query error), rather than us assuming
the round trip is fine.
"""

from __future__ import annotations

from app.config.settings import DEFAULT_EMBEDDING_DIM
from app.vectorstore import RetrievedChunk
from .conftest import requires_db


def _unit_vector(index: int, dim: int = DEFAULT_EMBEDDING_DIM) -> list[float]:
    """A one-hot vector: 1.0 at ``index``, 0.0 elsewhere."""
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


@requires_db
def test_known_embedding_round_trips_with_sane_scores(store, org_cleanup):
    org_id = store.create_organization("Roundtrip Co")
    org_cleanup.append(org_id)

    # Two orthogonal known vectors -> cosine similarity 0 between them.
    vec_a = _unit_vector(0)
    vec_b = _unit_vector(1)

    store.add_document(
        org_id=org_id,
        title="Known Vectors",
        chunks=["chunk A (aligned with query)", "chunk B (orthogonal to query)"],
        embeddings=[vec_a, vec_b],
    )

    # Query with a vector identical to vec_a.
    hits = store.query(org_id=org_id, query_embedding=vec_a, top_k=2)

    assert len(hits) == 2
    assert all(isinstance(h, RetrievedChunk) for h in hits)

    top, second = hits[0], hits[1]

    # 1) Most similar chunk is A, and its score is ~1.0 (identical vectors).
    assert top.content == "chunk A (aligned with query)"
    assert top.score == pytest_approx(1.0)

    # 2) The orthogonal chunk comes second with score ~0.0.
    assert second.content == "chunk B (orthogonal to query)"
    assert abs(second.score) < 0.01

    # 3) Ordering is by descending similarity.
    assert top.score > second.score


def pytest_approx(value: float, tol: float = 1e-3) -> "object":
    """Tiny local approx helper (avoids importing pytest just for this)."""

    class _Approx:
        def __eq__(self, other: float) -> bool:
            return abs(other - value) <= tol

        def __repr__(self) -> str:  # pragma: no cover - debugging aid
            return f"~{value}±{tol}"

    return _Approx()
