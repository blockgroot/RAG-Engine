"""Ask one grounded question against a single organization's stored content.

Runs the Policy Agent (embed → org-scoped retrieve → gate → grounded generate,
with the Phase 5 web-search fallback) against whatever was ingested for the given
org_id — e.g. the org that scripts/ingest_notion.py just created. The
retrieve/gate/generate logic itself lives in the agent (Phase 7), not here.

Run:
    python scripts/ask.py <org_id> "How many days of annual leave do we get?"
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/ask.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.agent import build_policy_agent
from app.core.exceptions import ProviderError
from app.db import close_pool


def main() -> int:
    load_dotenv()
    if len(sys.argv) < 3:
        print('Usage: python scripts/ask.py <org_id> "<question>"')
        return 2

    org_id = sys.argv[1]
    question = " ".join(sys.argv[2:])

    try:
        agent = build_policy_agent()
        response = agent.answer(question, org_id=org_id)

        print(f"\nQuestion: {question}")
        print("=" * 70)
        print("ANSWER:" if response.grounded else "ANSWER (fallback — not grounded):")
        print(response.answer.strip())
        print("=" * 70)
        print(f"grounded   : {response.grounded}")
        print(f"source     : {response.source}  (policy = internal docs, web = web search, none = fallback)")
        print(f"top_score  : {response.top_score}")
        if response.citations:
            print(f"grounded on {len(response.citations)} chunk(s):")
            for i, cit in enumerate(response.citations, 1):
                preview = cit.content.replace("\n", " ").strip()[:80]
                score = f"{cit.score:.3f}" if cit.score is not None else "n/a"
                print(f"  [{i}] score={score}  {preview}...")
        return 0

    except ProviderError as exc:
        print(f"\nQuery FAILED: {exc}")
        if exc.cause:
            print(f"cause: {exc.cause}")
        return 1
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
