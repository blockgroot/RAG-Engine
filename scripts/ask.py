"""Ask one grounded question against a single organization's stored content.

Runs the Phase 3 RAG pipeline (embed → org-scoped retrieve → gate → grounded
generate) against whatever was ingested for the given org_id — e.g. the org that
scripts/ingest_notion.py just created.

Run:
    python scripts/ask.py <org_id> "How many days of annual leave do we get?"
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/ask.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.core.exceptions import ProviderError
from app.db import close_pool
from app.rag import build_rag_pipeline


def main() -> int:
    load_dotenv()
    if len(sys.argv) < 3:
        print('Usage: python scripts/ask.py <org_id> "<question>"')
        return 2

    org_id = sys.argv[1]
    question = " ".join(sys.argv[2:])

    try:
        rag = build_rag_pipeline()
        result = rag.answer(question, org_id=org_id)

        print(f"\nQuestion: {question}")
        print("=" * 70)
        print("ANSWER:" if result.answered else "ANSWER (fallback — not grounded):")
        print(result.answer.strip())
        print("=" * 70)
        print(f"answered   : {result.answered}")
        print(f"top_score  : {result.top_score}")
        if result.sources:
            print(f"grounded on {len(result.sources)} chunk(s):")
            for i, src in enumerate(result.sources, 1):
                preview = src.content.replace("\n", " ").strip()[:80]
                print(f"  [{i}] score={src.score:.3f}  {preview}...")
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
