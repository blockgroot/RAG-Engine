"""Phase 15 before/after latency on a real multi-turn conversation.

Seeds a tiny corpus, runs 5 turns with MEMORY_RECENT_TURNS=3, and reports
wall time of ``answer()`` vs the deferred fold drain on turn 4+ — the fold
duration is what used to sit on the critical path before Phase 15.

Usage:
    .venv/bin/python scripts/measure_summary_fold_latency.py
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.config.settings import MemorySettings, ReuseSettings
from app.db import close_pool, get_connection
from app.embeddings import build_embedding_provider
from app.ingestion import chunk_text, preprocess
from app.llm import build_llm_provider
from app.memory import build_conversation_store
from app.rag.pipeline import RagPipeline
from app.rag.summary_fold import (
    shutdown_summary_folds,
    wait_for_conversation_fold,
    wait_for_pending_summary_folds,
)
from app.vectorstore import build_vector_store

DOC = """
# Paid Annual Leave — Full-Time Employees
Full-time employees are entitled to 25 days of paid annual leave per calendar
year. Up to 5 unused days may be carried over into the following year. Leave
requests must be submitted at least two weeks in advance.

# Sick Leave
Employees receive 10 paid sick days per year. A doctor's note is required after
3 consecutive sick days.

# Remote Work
Employees may work remotely up to 3 days per week with manager approval.
"""

QUESTIONS = [
    "How many paid annual leave days do full-time employees get?",
    "How many paid sick days do we get?",
    "Can we work remotely?",
    "How far in advance must leave be requested?",
    "And how many of those annual leave days can be carried over?",
]


def main() -> int:
    store = build_vector_store()
    embedder = build_embedding_provider()
    org_id = store.create_organization(f"fold-latency-{uuid.uuid4().hex[:8]}")
    text = preprocess(DOC)
    chunks = chunk_text(text)
    embeddings = embedder.embed(chunks)
    store.add_document(org_id, "HR Policies", chunks, embeddings, source_uri="measure://fold")
    print(f"Seeded org {org_id} with {len(chunks)} chunks")

    memory = build_conversation_store()
    pipe = RagPipeline(
        llm=build_llm_provider(),
        embedder=embedder,
        store=store,
        memory=memory,
        memory_settings=MemorySettings(recent_turns=3),
        reuse_settings=ReuseSettings(enabled=False),
    )
    cid = memory.create_conversation(org_id)

    print("\nTurn | answer_ms | fold_drain_ms | notes")
    print("-" * 72)
    for i, q in enumerate(QUESTIONS, start=1):
        t0 = time.perf_counter()
        result = pipe.answer(q, org_id, conversation_id=cid)
        answer_ms = (time.perf_counter() - t0) * 1000

        t1 = time.perf_counter()
        wait_for_conversation_fold(cid, timeout=90.0)
        fold_ms = (time.perf_counter() - t1) * 1000

        if i <= 3:
            note = "window filling (fold is no-op)"
        elif i == 4:
            note = (
                f"FOLD — before user waited ~{answer_ms + fold_ms:.0f}ms; "
                f"after sees answer at {answer_ms:.0f}ms"
            )
        else:
            note = (
                f"summary={memory.get_summary(cid) is not None}; "
                f"resolved={result.resolved_question!r}"
            )

        print(f"{i:4d} | {answer_ms:9.0f} | {fold_ms:13.0f} | {note}")

    wait_for_pending_summary_folds(timeout=30.0)
    summary = memory.get_summary(cid)
    print("\nFinal summary present:", summary is not None)
    if summary:
        print("Summary preview:", summary[:240].replace("\n", " "))
    print("Verbatim turns:", len(memory.get_turns(cid)))
    print("Turn-5 answered:", result.answered, "| answer snippet:", result.answer[:120])

    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s", (org_id,))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutdown_summary_folds(wait=True, timeout=30.0)
        close_pool()
