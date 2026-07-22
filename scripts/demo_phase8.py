"""Phase 8 demo: incremental summarization + retrieval reuse, on live Notion data.

Runs ONE long multi-turn conversation against an ingested org and, per turn, prints:
  - the standalone question the follow-up was rewritten to,
  - whether retrieval was REUSED from the previous turn or run FRESH (+ gate score),
  - the grounded answer, and
  - the running-summary state (how many turns are still verbatim + the summary text)

so both Phase 8 mechanisms are visible directly. Run it twice for a before/after:

    # AFTER  (reuse on — the new default)
    python scripts/demo_phase8.py <org_id>
    # BEFORE (reuse off — every turn retrieves fresh, the pre-Phase-8 behaviour)
    RETRIEVAL_REUSE_ENABLED=false python scripts/demo_phase8.py <org_id>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.config.settings import MemorySettings, ReuseSettings
from app.core.exceptions import ProviderError
from app.db import close_pool
from app.memory import build_conversation_store
from app.rag import build_rag_pipeline

# A conversation that (a) exceeds the 3-turn verbatim window several times, so the
# running summary is updated incrementally turn after turn, (b) includes a
# repeat/clarify follow-up that should reuse the previous chunks, and (c) ends by
# referring back to turn 1 — which by then lives only in the running summary.
TURNS = [
    "Who is eligible for the health allowance?",
    "What kinds of expenses can I claim under it?",
    "What is the annual limit on the allowance?",
    "Sorry, can you repeat that limit?",          # <- expect REUSE of turn 3's chunks
    "What happens if someone misuses the allowance?",
    "How long does the pre-approval review take?",
    "And remind me — who was eligible for it again?",  # <- depends on turn 1 (summarized)
]


def main() -> int:
    load_dotenv()
    if len(sys.argv) < 2:
        print("Usage: python scripts/demo_phase8.py <org_id>")
        return 2
    org_id = sys.argv[1]

    memory = build_conversation_store()
    reuse = ReuseSettings.from_env()
    window = MemorySettings.from_env().recent_turns
    pipe = build_rag_pipeline(memory=memory)

    print(f"reuse: {'ON' if reuse.enabled else 'OFF'} (threshold {reuse.threshold})  "
          f"| verbatim window: {window} turns\n")

    try:
        cid = memory.create_conversation(org_id)
        for i, question in enumerate(TURNS, 1):
            r = pipe.answer(question, org_id, conversation_id=cid)
            score = f"{r.top_score:.3f}" if r.top_score is not None else "n/a"
            mode = "REUSED prev chunks" if r.retrieval_reused else "fresh retrieval  "
            print(f"── Turn {i} " + "─" * 56)
            print(f"  you       : {question}")
            if r.resolved_question and r.resolved_question != question:
                print(f"  rewritten : {r.resolved_question}")
            print(f"  retrieval : {mode}   (gate score {score})")
            print(f"  answer    : {' '.join(r.answer.split())[:240]}")
            # Running-summary state after this turn.
            turns = memory.get_turns(cid)
            summary = memory.get_summary(cid)
            print(f"  memory    : {len(turns)} turn(s) kept verbatim; "
                  f"summary {'set' if summary else 'empty'}")
            if summary:
                print(f"  summary → : {' '.join(summary.split())[:240]}")
            print()
        return 0
    except ProviderError as exc:
        print(f"\nDemo FAILED: {exc}")
        return 1
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
