"""Conversation-memory tests (Phase 5, Capability A).

Prove that follow-up questions are rewritten into standalone questions using
prior context and then retrieve the right internal content, against real ingested
policy data — and that once a conversation grows past the summarization threshold,
older turns are compressed while a question depending on that earlier (now
summarized) context still resolves.
"""

from __future__ import annotations

import uuid

from app.config.settings import MemorySettings, ReuseSettings
from app.ingestion import chunk_text, preprocess
from app.llm import build_llm_provider
from app.rag.pipeline import RagPipeline
from .conftest import requires_db, requires_llm

# Two distinct documents so full-time vs part-time leave are separate chunks and
# retrieval genuinely has to pick the right one.
FULL_TIME_DOC = """
# Paid Annual Leave — Full-Time Employees
Full-time employees are entitled to 25 days of paid annual leave per calendar
year. Up to 5 unused days may be carried over into the following year. Leave
requests must be submitted at least two weeks in advance.
"""

PART_TIME_DOC = """
# Paid Annual Leave — Part-Time Employees
Part-time employees receive 12 days of paid annual leave per year, pro-rated by
the number of hours they work each week.
"""

SICK_DOC = """
# Sick Leave
Employees receive 10 paid sick days per year, separate from annual leave.
"""

REMOTE_DOC = """
# Remote Work
Employees may work remotely up to 3 days per week with manager approval.
"""


def _ingest(store, embedder, org_id, title, text):
    chunks = chunk_text(preprocess(text))
    store.add_document(org_id, title, chunks, embedder.embed(chunks))


def _seed(store, embedder, org_cleanup, docs):
    org_id = store.create_organization(f"Convo Co-{uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    for title, text in docs:
        _ingest(store, embedder, org_id, title, text)
    return org_id


@requires_db
@requires_llm
def test_followup_is_rewritten_and_retrieves_right_content(rag_convo, store, embedder, memory, org_cleanup):
    org_id = _seed(
        store, embedder, org_cleanup,
        [("Full-Time Leave", FULL_TIME_DOC), ("Part-Time Leave", PART_TIME_DOC)],
    )
    cid = memory.create_conversation(org_id)

    # Turn 1: a standalone question about full-time leave.
    r1 = rag_convo.answer(
        "How many days of paid annual leave do full-time employees get?",
        org_id, conversation_id=cid,
    )
    assert r1.answered, r1.answer
    assert "25" in r1.answer, r1.answer

    # Turn 2: a follow-up that is meaningless on its own.
    r2 = rag_convo.answer("what about for part-timers?", org_id, conversation_id=cid)

    # It was rewritten into a standalone question mentioning part-time...
    assert r2.resolved_question is not None
    assert "part" in r2.resolved_question.lower(), r2.resolved_question
    # ...and retrieved the PART-TIME chunk (not the full-time one) and answered 12.
    assert r2.answered, r2.answer
    assert "12" in r2.answer, r2.answer
    assert any("Part-Time" in s.content or "12" in s.content for s in r2.sources)


@requires_db
@requires_llm
def test_incremental_summary_compresses_old_turns_but_early_context_still_resolves(
    store, embedder, memory, org_cleanup
):
    # Phase 8: incremental summarization. Window of 2 verbatim turns; every turn
    # beyond that is folded into the running summary one at a time. Reuse is off so
    # this test isolates the memory/summarization behaviour.
    pipe = RagPipeline(
        llm=build_llm_provider(),
        embedder=embedder,
        store=store,
        memory=memory,
        memory_settings=MemorySettings(recent_turns=2),
        reuse_settings=ReuseSettings(enabled=False),
    )
    org_id = _seed(
        store, embedder, org_cleanup,
        [
            ("Full-Time Leave", FULL_TIME_DOC),
            ("Sick Leave", SICK_DOC),
            ("Remote Work", REMOTE_DOC),
        ],
    )
    cid = memory.create_conversation(org_id)

    # Turn 1 establishes the "annual leave" topic (25 days, carry over 5).
    pipe.answer("How many paid annual leave days do full-time employees get?", org_id, conversation_id=cid)
    # Several more turns; each one beyond the window is incrementally summarized.
    pipe.answer("How many paid sick days do we get?", org_id, conversation_id=cid)
    pipe.answer("Can we work remotely?", org_id, conversation_id=cid)
    pipe.answer("How far in advance must leave be requested?", org_id, conversation_id=cid)

    # The verbatim window stayed capped at 2, and a running summary was built up
    # continuously (turn 1's content now lives only in that summary).
    assert memory.get_summary(cid) is not None, "expected a continuously-built running summary"
    assert len(memory.get_turns(cid)) == 2, "expected verbatim turns capped at the window"

    # Turn 5 depends on turn 1 (now only in the summary): "those annual leave days".
    r5 = pipe.answer(
        "And how many of those annual leave days can be carried over to next year?",
        org_id, conversation_id=cid,
    )
    # Rewritten to a standalone question about carrying over annual leave...
    assert r5.resolved_question is not None
    assert "annual leave" in r5.resolved_question.lower() or "carr" in r5.resolved_question.lower(), \
        r5.resolved_question
    # ...and correctly answered from the full-time doc (5 carry-over days).
    assert r5.answered, r5.answer
    assert "5" in r5.answer, r5.answer
