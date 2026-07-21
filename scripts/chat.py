"""Run a multi-turn conversation against one org (Phase 5, Capability A).

Creates a conversation and asks each provided question as a turn, so follow-ups
are resolved against prior turns. Prints, per turn, the standalone question the
pipeline rewrote to (if any) and the grounded answer.

Run (each quoted arg is one turn):
    python scripts/chat.py <org_id> "How many annual leave days do we get?" "what about part-timers?"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.core.exceptions import ProviderError
from app.db import close_pool
from app.memory import build_conversation_store
from app.rag import build_rag_pipeline


def main() -> int:
    load_dotenv()
    if len(sys.argv) < 3:
        print('Usage: python scripts/chat.py <org_id> "question 1" "question 2" ...')
        return 2

    org_id = sys.argv[1]
    questions = sys.argv[2:]

    try:
        memory = build_conversation_store()
        rag = build_rag_pipeline(memory=memory)  # memory + web search from config
        conversation_id = memory.create_conversation(org_id)
        print(f"conversation {conversation_id} (org {org_id})\n")

        for i, question in enumerate(questions, 1):
            result = rag.answer(question, org_id, conversation_id=conversation_id)
            print(f"── Turn {i} " + "─" * 50)
            print(f"you       : {question}")
            if result.resolved_question and result.resolved_question != question:
                print(f"rewritten : {result.resolved_question}")
            tag = {"policy": "[policy]", "web": "[web]", "none": "[no answer]"}.get(
                result.source, result.source
            )
            print(f"assistant {tag}: {result.answer.strip()}\n")
        return 0

    except ProviderError as exc:
        print(f"\nChat FAILED: {exc}")
        if exc.cause:
            print(f"cause: {exc.cause}")
        return 1
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
