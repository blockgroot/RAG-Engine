"""Run the golden-set evaluation and write a Markdown report.

Modes
-----
    python -m evaluation.run_eval                 # path-firing only (deterministic)
    python -m evaluation.run_eval --skip-web      # path-firing, excluding web cases
    python -m evaluation.run_eval --ragas          # + RAGAS scoring (expensive)

Exit code is non-zero if any path-firing case fails or (with --ragas) any RAGAS
mean falls below baseline — so CI can gate on it directly.

It seeds a throwaway org from ``CORPUS``, runs every case through a production-config
``PolicyAgent`` (memory ON; web ON unless --skip-web), writes the report, and
cascade-deletes the org. Requires DATABASE_URL + a configured LLM (same prereqs as
the test suite).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.agent import build_policy_agent
from app.core.exceptions import ProviderError
from app.db import close_pool, get_connection
from app.embeddings import build_embedding_provider
from app.memory import build_conversation_store
from app.vectorstore import build_vector_store

from .golden_set import GOLDEN_CASES
from .harness import run_golden_set, seed_corpus
from .report import build_report
from .ragas_scoring import ragas_available, score_cases

DEFAULT_OUT = Path(__file__).resolve().parent / "reports" / "latest.md"


def _delete_org(org_id: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the golden-set evaluation.")
    parser.add_argument("--ragas", action="store_true", help="also run RAGAS scoring")
    parser.add_argument("--skip-web", action="store_true", help="exclude web-search cases")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report output path")
    args = parser.parse_args()

    cases = [c for c in GOLDEN_CASES if not (args.skip_web and c.category == "web")]

    from app.config.settings import RagSettings
    threshold = RagSettings.from_env().similarity_threshold

    try:
        embedder = build_embedding_provider()
        store = build_vector_store()
        memory = build_conversation_store()
        # Production config: memory ON; web ON (from config) unless excluded for the
        # fast tier, in which case we pass web_search=None to turn it OFF. Omitting
        # the kwarg entirely lets build_rag_pipeline build it from config.
        agent_kwargs = dict(embedder=embedder, store=store, memory=memory)
        if args.skip_web:
            agent_kwargs["web_search"] = None
        agent = build_policy_agent(**agent_kwargs)

        org_id = seed_corpus(store, embedder, f"Golden Set-{uuid.uuid4().hex[:8]}")
        try:
            results = run_golden_set(agent, org_id, memory=memory, cases=cases)
        finally:
            _delete_org(org_id)

        ragas_report = None
        if args.ragas:
            if not ragas_available():
                print("RAGAS not installed — run `pip install -e '.[eval]'`. Skipping scoring.")
            else:
                ragas_report = score_cases(results)

        report = build_report(
            results,
            threshold=threshold,
            ragas=ragas_report,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report)

        # Console summary. Web cases are network-dependent (DuckDuckGo rate-limits;
        # a failed search degrades to the internal fallback by design), so they are
        # ADVISORY — reported, but they never gate the exit code. Only a web case
        # that answers as `policy` (fabrication) would be a real failure.
        gating = [r for r in results if r.case.category != "web"]
        passed = sum(1 for r in gating if r.passed)
        print(f"\nPath-firing (gating): {passed}/{len(gating)} passed. Report -> {args.out}")
        for r in results:
            if r.case.category == "web":
                note = "web fired" if r.source == "web" else f"advisory: {r.source} (network)"
                print(f"  WEB  {r.case.id}: {note}")
                continue
            if not r.passed:
                print(f"  FAIL {r.case.id}: expected {r.case.expected_source}, "
                      f"got {r.source} (facts_ok={r.facts_ok}, resolved_ok={r.resolved_ok})")

        failed = passed != len(gating) or any(
            r.source == "policy" for r in results if r.case.category == "web"
        )
        if ragas_report is not None:
            print(f"RAGAS means: " + ", ".join(
                f"{m}={ragas_report.means.get(m, float('nan')):.3f}"
                for m in ragas_report.means
            ))
            if not ragas_report.passed:
                print(f"  RAGAS below baseline: {', '.join(ragas_report.failures)}")
                failed = True

        return 1 if failed else 0

    except ProviderError as exc:
        print(f"\nEval FAILED: {exc}")
        if exc.cause:
            print(f"cause: {exc.cause}")
        return 2
    finally:
        close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
