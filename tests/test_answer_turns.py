"""The answer prompt may see earlier turns (MEMORY_ANSWER_TURNS), off by default.

When on, the turns sit after CONTEXT (MODE first, CONTEXT not moved earlier),
fenced, labelled as not evidence, with their old [n] markers removed so they
cannot be mistaken for this turn's blocks.
"""

from __future__ import annotations

from app.config.settings import MemorySettings, RagSettings, RecoverySettings, ReuseSettings
from app.rag import prompts
from app.rag.pipeline import RagPipeline

from .fakes import InMemoryConversationStore, KeywordEmbedder, RecordingLLM, RecordingVectorStore

ORG = "org-1"


def test_no_turns_leaves_the_prompt_unchanged():
    base = prompts.build_grounded_prompt("q?", ["Leave is 25 days."], "idk")
    assert prompts.build_grounded_prompt("q?", ["Leave is 25 days."], "idk", earlier_turns=()) == base
    assert "CONVERSATION SO FAR" not in base


def test_turns_sit_after_context_without_their_old_markers():
    prompt = prompts.build_grounded_prompt(
        "and the second one?", ["Leave is 25 days."], "idk",
        earlier_turns=(("What leave types are there?", "Sick leave [1]. Casual leave [2]."),),
    )
    assert prompt.index("CONTEXT:") < prompt.index("CONVERSATION SO FAR") < prompt.index("QUESTION:")
    assert "Casual leave." in prompt and "Casual leave [2]" not in prompt
    assert "NOT evidence" in prompt


def _pipeline(llm, memory, turns):
    return RagPipeline(
        llm=llm, embedder=KeywordEmbedder(),
        store=RecordingVectorStore(ORG, content="leave: full-time employees get 25 days"),
        settings=RagSettings(top_k=5, similarity_threshold=0.35, fallback_response="idk"),
        memory=memory, web_search=None,
        memory_settings=MemorySettings(recent_turns=3, answer_turns=turns),
        retriever=None, reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
    )


def _grounded_prompts(turns):
    llm = RecordingLLM(rewrite="How many annual leave days do we get?")
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, memory, turns)
    cid = memory.create_conversation(ORG)
    pipe.answer("How many annual leave days do we get?", ORG, conversation_id=cid)
    pipe.answer("and is it paid?", ORG, conversation_id=cid)
    return [p for p in llm.prompts if "CONTEXT:" in p and "QUESTION:" in p]


def test_off_by_default_the_answer_never_sees_the_chat():
    assert MemorySettings().answer_turns == 0
    assert all("CONVERSATION SO FAR" not in p for p in _grounded_prompts(0))


def test_on_the_follow_up_answer_sees_the_earlier_turn():
    first, second = _grounded_prompts(1)[-2:]
    assert "CONVERSATION SO FAR" not in first  # nothing earlier on the first turn
    assert "CONVERSATION SO FAR" in second and "How many annual leave days" in second


# -- an earlier answer is shown only while its sources are still the asker's ----

DOC = "11111111-1111-1111-1111-111111111111"


def _gated(monkeypatch, readable, cited):
    from app.rag import reshape

    monkeypatch.setattr(reshape, "readable_unchanged", lambda ids, **kw: readable(ids))
    llm = RecordingLLM(rewrite="How many annual leave days do we get?")
    memory = InMemoryConversationStore()
    pipe = _pipeline(llm, memory, 1)
    cid = memory.create_conversation(ORG)
    memory.append_turn(cid, "How many leave days?", "SECRET 25 days [1].", cited)
    pipe.answer("and is it paid?", ORG, conversation_id=cid)
    return [p for p in llm.prompts if "CONVERSATION SO FAR" in p][-1]


def test_an_answer_whose_sources_are_still_readable_is_shown(monkeypatch):
    prompt = _gated(monkeypatch, lambda ids: set(ids), [{"n": 1, "document_id": DOC}])
    assert "SECRET 25 days." in prompt


def test_an_answer_whose_source_was_revoked_shows_only_the_question(monkeypatch):
    prompt = _gated(monkeypatch, lambda ids: set(), [{"n": 1, "document_id": DOC}])
    assert "SECRET" not in prompt and "How many leave days?" in prompt
    assert "(answer not shown)" in prompt


def test_an_uncited_answer_shows_only_the_question(monkeypatch):
    prompt = _gated(monkeypatch, lambda ids: set(ids), [])
    assert "SECRET" not in prompt


def test_a_failed_re_check_shows_only_the_question(monkeypatch):
    def broken(ids):
        raise RuntimeError("db down")

    prompt = _gated(monkeypatch, broken, [{"n": 1, "document_id": DOC}])
    assert "SECRET" not in prompt
