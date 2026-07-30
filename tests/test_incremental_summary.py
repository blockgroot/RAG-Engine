"""Incremental-summarization tests (Phase 8, Part 1).

Deterministic unit tests (no DB/LLM/model — see tests/fakes.py). They prove the
running summary is updated *incrementally*:

- after the verbatim window fills, the summary is updated after EVERY turn (a
  continuous stream of small updates), not built all at once when a threshold
  trips;
- each update's input is the existing summary plus exactly ONE turn (the one that
  just fell out of the window) — so the per-update cost stays small and does not
  grow as the conversation gets longer.

The coherence / "early context still resolves after many rounds" property is
proven end-to-end against the real LLM + Notion data in test_conversation.py.
"""

from __future__ import annotations

from app.config.settings import MemorySettings, RagSettings, RecoverySettings, ReuseSettings
from app.rag.pipeline import RagPipeline
from app.rag.summary_fold import wait_for_pending_summary_folds
from .fakes import (
    InMemoryConversationStore,
    KeywordEmbedder,
    RecordingLLM,
    RecordingVectorStore,
)

ORG = "org-1"
WINDOW = 3


def _count_folded_turns(summary_prompt: str) -> int:
    """How many turns a summary-update prompt folds in == its 'User:' occurrences.

    The canned existing-summary text contains no 'User:' marker, so every 'User:'
    in the prompt is a turn in its NEW TURNS block.
    """
    return summary_prompt.count("User:")


def _drive(n_turns: int):
    llm = RecordingLLM(rewrite="How many paid annual leave days do we get?")
    store = RecordingVectorStore(ORG, content="leave: full-time employees get 25 days")
    memory = InMemoryConversationStore()
    pipe = RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(top_k=5, similarity_threshold=0.35, fallback_response="idk"),
        memory=memory,
        web_search=None,
        memory_settings=MemorySettings(recent_turns=WINDOW),
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),  # isolate summarization behaviour
        recovery_settings=RecoverySettings(enabled=False),
    )
    cid = memory.create_conversation(ORG)
    for i in range(n_turns):
        pipe.answer(f"leave question number {i}?", ORG, conversation_id=cid)
    # Phase 15: folds run in the background — drain before asserting.
    wait_for_pending_summary_folds(timeout=5.0)
    return llm, memory, cid


def test_summary_updates_incrementally_after_every_turn_past_the_window():
    # 8 turns, window of 3. No summary while the window is filling (turns 1-3);
    # then one summary update per turn for turns 4..8 (a continuous stream).
    llm, memory, cid = _drive(8)

    assert len(llm.summary_prompts) == 8 - WINDOW, (
        "expected one incremental summary update per turn once the window is full, "
        "not a single bulk summarization"
    )
    # The verbatim window never exceeds its size — older turns were folded away.
    assert len(memory.get_turns(cid)) == WINDOW
    assert memory.get_summary(cid) is not None


def test_each_summary_update_folds_exactly_one_turn_and_input_does_not_grow():
    # This is the cost guarantee: every update's input is the summary + ONE turn,
    # regardless of how long the conversation has become.
    llm, memory, cid = _drive(10)

    folded = [_count_folded_turns(p) for p in llm.summary_prompts]
    assert folded, "no summary updates were made"
    assert all(count == 1 for count in folded), (
        f"each update must fold exactly one turn; got per-update turn counts {folded}"
    )
    # Input size does NOT grow with conversation length. The very first update has
    # no prior summary (shorter); from the second update on — when the running
    # summary is present, i.e. the steady state of a long conversation — the input
    # is identical every time (existing summary + one turn), never accumulating.
    steady = [len(p) for p in llm.summary_prompts[1:]]
    assert len(set(steady)) == 1, (
        f"steady-state summary-update input size must stay constant, saw {steady}"
    )


def test_no_summary_before_the_window_fills():
    # With 3 turns and a window of 3, nothing has fallen out yet -> no summary.
    llm, memory, cid = _drive(WINDOW)
    assert llm.summary_prompts == []
    assert memory.get_summary(cid) is None
    assert len(memory.get_turns(cid)) == WINDOW
