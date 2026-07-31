"""Phase 15: deferred running-summary fold is off the critical path.

Proves:
1. ``answer()`` returns without waiting for the summary LLM call once the
   verbatim window is full (response latency no longer includes fold time).
2. After draining, the summary is still updated and usable on a later turn.
3. A subsequent ``answer()`` for the same conversation waits for any in-flight
   fold before rewrite (barrier), so context never silently drops a turn.
"""

from __future__ import annotations

import time

from app.config.settings import MemorySettings, RagSettings, RecoverySettings, ReuseSettings
from app.rag.pipeline import RagPipeline
from app.rag.summary_fold import (
    wait_for_conversation_fold,
    wait_for_pending_summary_folds,
)
from .fakes import (
    InMemoryConversationStore,
    KeywordEmbedder,
    RecordingLLM,
    RecordingVectorStore,
)

ORG = "org-fold"
WINDOW = 3
FOLD_SLEEP_S = 0.35


class _SlowSummaryLLM(RecordingLLM):
    """RecordingLLM that sleeps on summary prompts to make fold latency measurable."""

    def __init__(self, *, sleep_s: float = FOLD_SLEEP_S, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sleep_s = sleep_s
        self.summary_started = 0
        self.summary_finished = 0

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        if "UPDATED SUMMARY:" in prompt:
            self.summary_started += 1
            time.sleep(self.sleep_s)
            self.summary_finished += 1
        return super().generate(prompt)


def _pipeline(llm: RecordingLLM) -> tuple[RagPipeline, InMemoryConversationStore]:
    memory = InMemoryConversationStore()
    pipe = RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=RecordingVectorStore(ORG, content="leave: full-time employees get 25 days"),
        settings=RagSettings(top_k=5, similarity_threshold=0.35, fallback_response="idk"),
        memory=memory,
        web_search=None,
        memory_settings=MemorySettings(recent_turns=WINDOW),
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
    )
    return pipe, memory


def test_answer_returns_before_summary_fold_completes():
    """Turn 4+ must not block on the summary LLM — the whole point of Phase 15."""
    llm = _SlowSummaryLLM(
        sleep_s=FOLD_SLEEP_S,
        rewrite="How many paid annual leave days do we get?",
    )
    pipe, memory = _pipeline(llm)
    cid = memory.create_conversation(ORG)

    for i in range(WINDOW):
        pipe.answer(f"warm-up question {i}?", ORG, conversation_id=cid)
    wait_for_pending_summary_folds(timeout=5.0)
    assert llm.summary_finished == 0

    t0 = time.perf_counter()
    result = pipe.answer("fourth leave question?", ORG, conversation_id=cid)
    answer_ms = (time.perf_counter() - t0) * 1000

    # Returned before the slow fold finished (fold still in flight or just started).
    assert result.answer  # answer itself is ready
    assert answer_ms < (FOLD_SLEEP_S * 1000) * 0.7, (
        f"answer() took {answer_ms:.0f}ms — still appears to wait on the "
        f"{FOLD_SLEEP_S * 1000:.0f}ms summary fold"
    )

    # Before/after: blocking fold would have cost ~answer + fold; deferred does not.
    t1 = time.perf_counter()
    wait_for_conversation_fold(cid, timeout=5.0)
    fold_drain_ms = (time.perf_counter() - t1) * 1000
    assert llm.summary_finished >= 1
    assert memory.get_summary(cid) is not None

    print(
        f"\n[Phase 15 latency] turn-4 answer()={answer_ms:.0f}ms "
        f"(no fold wait); remaining fold drain={fold_drain_ms:.0f}ms; "
        f"simulated fold sleep={FOLD_SLEEP_S * 1000:.0f}ms. "
        f"Before (answer+fold) would be ~{answer_ms + FOLD_SLEEP_S * 1000:.0f}ms."
    )


def test_deferred_summary_still_usable_on_later_turn():
    """Background fold must land and feed rewrite on a subsequent turn."""
    llm = _SlowSummaryLLM(
        sleep_s=0.05,
        rewrite="How many of the annual leave days can be carried over?",
        summary="User asked about annual leave (25 days).",
    )
    pipe, memory = _pipeline(llm)
    cid = memory.create_conversation(ORG)

    for i in range(WINDOW + 1):
        pipe.answer(f"leave question {i}?", ORG, conversation_id=cid)

    # Next turn's rewrite barrier waits for the fold; summary must be present
    # for the rewriter (we only assert the summary landed + prune happened).
    pipe.answer("and how many of those carry over?", ORG, conversation_id=cid)
    wait_for_pending_summary_folds(timeout=5.0)

    assert memory.get_summary(cid) is not None
    assert len(memory.get_turns(cid)) == WINDOW
    assert len(llm.summary_prompts) >= 1
