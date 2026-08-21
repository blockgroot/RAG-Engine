#!/usr/bin/env python3
"""Multi-run red-team probe: prompt injection + bias-probing golden cases.

Runs each red-team golden case (``injection-*`` and ``bias-*`` — see
``evaluation/golden_set.py``) N times *without* the retry harness, so
pass-rate is honest (not inflated by ``run_case_stable``). The ``bias-*``
cases were added to widen this beyond pure prompt injection (the
stress-testing/red-teaming gap in docs/RAG_VIDEO_COMPARISON.md) — a leading
discriminatory question with no supporting corpus content is a different
failure mode than an embedded "ignore previous instructions" payload, and
needed its own cases rather than being folded into the injection ones.

Usage:
    .venv/bin/python scripts/probe_injection.py
    .venv/bin/python scripts/probe_injection.py --runs 15
"""

from __future__ import annotations

import argparse
import re
import uuid
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.agent import build_policy_agent
from app.config.settings import RecoverySettings
from app.db import close_pool, get_connection
from app.embeddings import build_embedding_provider
from app.rag import shutdown_summary_folds
from app.rag.retrieval import HybridRetriever
from app.reranker import build_reranker
from app.vectorstore import build_vector_store
from evaluation.golden_set import GOLDEN_CASES
from evaluation.harness import run_case, seed_corpus


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=15)
    parser.add_argument(
        "--fail-on-leak",
        action="store_true",
        help=(
            "Exit non-zero if any run of any injection case leaks (CI red-team "
            "gate). Without this flag the script only reports rates, unchanged "
            "from its original manual-probe behaviour."
        ),
    )
    args = parser.parse_args()

    total_leaks = 0
    cases = [
        c for c in GOLDEN_CASES if c.id.startswith("injection-") or c.id.startswith("bias-")
    ]
    store = build_vector_store()
    embedder = build_embedding_provider()
    retriever = HybridRetriever(store=store, reranker=build_reranker())
    org_id = seed_corpus(store, embedder, f"InjectProbe-{uuid.uuid4().hex[:8]}")
    agent = build_policy_agent(
        embedder=embedder,
        store=store,
        memory=None,
        web_search=None,
        retriever=retriever,
        recovery_settings=RecoverySettings(enabled=False),
    )
    try:
        print(f"org={org_id} runs={args.runs}")
        for case in cases:
            ok = 0
            leaks = 0
            fact_miss = 0
            for attempt in range(1, args.runs + 1):
                r = run_case(agent, org_id, case)
                forbidden = bool(
                    case.forbidden_answer_pattern
                    and re.search(case.forbidden_answer_pattern, r.answer.lower())
                )
                facts = (
                    all(f.lower() in r.answer.lower() for f in case.expected_facts)
                    if case.expected_facts
                    else True
                )
                passed = r.path_ok and facts and not forbidden
                if passed:
                    ok += 1
                if forbidden:
                    leaks += 1
                    total_leaks += 1
                if not facts:
                    fact_miss += 1
                status = "PASS" if passed else "FAIL"
                leak_note = " LEAK" if forbidden else ""
                print(
                    f"[{case.id} {attempt:02d}/{args.runs}] {status}{leak_note} "
                    f"source={r.source} answer={r.answer[:120]!r}"
                )
            rate = 100.0 * ok / args.runs
            print(
                f"\n== {case.id} summary: {ok}/{args.runs} pass ({rate:.0f}%), "
                f"injection_leaks={leaks}, fact_misses={fact_miss} ==\n"
            )
    finally:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM organizations WHERE id = %s::uuid", (org_id,)
            )
        shutdown_summary_folds(wait=True, timeout=10)
        close_pool()

    if args.fail_on_leak and total_leaks:
        print(f"::error::injection probe found {total_leaks} leak(s) across all cases/runs")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
