"""GitHub and chart answers never enter RagPipeline, which is where a turn is saved.

A conversation with no turn is omitted from the history list, so without this
the chat disappears on reload.
"""

from __future__ import annotations

from app.api.chat import _keep_standalone_turn

from .fakes import InMemoryConversationStore


def _store(monkeypatch) -> tuple[InMemoryConversationStore, str]:
    memory = InMemoryConversationStore()
    cid = memory.create_conversation("org")
    monkeypatch.setattr("app.memory.build_conversation_store", lambda: memory)
    return memory, cid


def test_a_github_answer_is_kept_on_the_conversation(monkeypatch):
    memory, cid = _store(monkeypatch)
    _keep_standalone_turn("github", cid, "what does notes do?", "It stores meeting notes.")
    turn = memory.get_turns(cid)[-1]
    assert (turn.question, turn.answer) == ("what does notes do?", "It stores meeting notes.")


def test_a_chart_answer_is_kept_on_the_conversation(monkeypatch):
    memory, cid = _store(monkeypatch)
    _keep_standalone_turn("insights", cid, "how many pulls merged?", "12 this month.")
    assert memory.get_turns(cid)[-1].answer == "12 this month."


def test_an_indexed_answer_is_left_to_the_pipeline(monkeypatch):
    memory, cid = _store(monkeypatch)
    _keep_standalone_turn("notion", cid, "what is the leave policy?", "25 days.")
    assert memory.get_turns(cid) == []
