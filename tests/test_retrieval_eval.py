"""Phase 22: retrieval-only eval tier (no LLM) + production query signal logging."""

from __future__ import annotations

import json
import logging
import uuid
from unittest.mock import patch

import pytest

from app.db import get_connection
from evaluation.retrieval_eval import (
    RETRIEVAL_EVAL_CASES,
    RetrievalEvalCase,
    run_retrieval_case,
    run_retrieval_suite,
    seed_eval_corpus,
)
from .conftest import requires_db


@pytest.fixture(scope="module")
def retrieval_eval_org(store, embedder):
    org_id = seed_eval_corpus(store, embedder, f"RetrievalEval-{uuid.uuid4().hex[:8]}")
    yield org_id
    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))


@requires_db
def test_retrieval_suite_passes_on_golden_corpus(retriever, embedder, retrieval_eval_org):
    results = run_retrieval_suite(retriever, embedder, retrieval_eval_org)
    failures = [r for r in results if not r.ok]
    assert not failures, [
        (f.case.id, f.rank, f.case.max_rank, f.top_contents) for f in failures
    ]


@requires_db
def test_retrieval_regression_detected_when_ranking_breaks(
    retriever, embedder, retrieval_eval_org
):
    """A broken rank order fails retrieval-only checks even when generation might mask it."""
    case = next(c for c in RETRIEVAL_EVAL_CASES if c.id == "part-time-leave")
    baseline = run_retrieval_case(retriever, embedder, retrieval_eval_org, case)
    assert baseline.ok, f"baseline rank={baseline.rank}"

    real_retrieve = retriever.retrieve

    def _broken(org_id, question, query_vec, **kwargs):
        out = real_retrieve(org_id, question, query_vec, **kwargs)
        if "part-time" in question.lower():
            # Push the correct chunk to the bottom — simulates a retrieval regression.
            hits = list(out.hits)
            hits.sort(
                key=lambda h: (
                    1 if ("12" in h.content and "part-time" in h.content.lower()) else 0
                )
            )
            from app.rag.retrieval import RetrievalResult

            return RetrievalResult(hits=hits, gate_score=out.gate_score)
        return out

    with patch.object(retriever, "retrieve", side_effect=_broken):
        broken = run_retrieval_case(retriever, embedder, retrieval_eval_org, case)
    assert not broken.ok
    assert broken.rank is None or broken.rank > case.max_rank


@requires_db
def test_query_signal_log_emitted(rag, store, org_cleanup, caplog):
    org_id = store.create_organization(f"SignalLog-{uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    text = "Full-time employees receive 25 days of paid annual leave each year."
    store.add_document(org_id, "leave", [text], rag._embedder.embed([text]))

    with caplog.at_level(logging.INFO, logger="rag.query_signals"):
        result = rag.answer("How many annual leave days for full-time staff?", org_id)

    lines = [r.message for r in caplog.records if r.name == "rag.query_signals"]
    assert lines, "expected a query_signal log line"
    payload = json.loads(lines[-1])
    assert payload["event"] == "query_signal"
    assert payload["org_id"] == org_id
    assert payload["top_score"] is not None
    assert payload["answered"] == result.answered
    assert payload["source"] in ("policy", "none", "web")
    assert "response_mode" in payload
    assert "retrieval_reused" in payload
