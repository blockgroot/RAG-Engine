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


PANEL = {"chart": "bar", "group_by": "priority", "unit": "issues",
         "points": [{"group": "High", "value": 4}, {"group": "Low", "value": 2}],
         "details": [{"subject": "Fix login", "group": "High"}]}


def test_a_chart_answer_keeps_its_chart_for_reopening(monkeypatch):
    from app.memory.conversations import chart_for_history, chart_for_storage

    memory, cid = _store(monkeypatch)
    _keep_standalone_turn("insights", cid, "chart issues by priority", "Issues by priority.",
                          None, chart_for_storage(PANEL, "month"))
    stored = memory.get_turns(cid)[-1].chart
    assert chart_for_history(stored) == {"panel": PANEL, "period": "month"}


def test_no_chart_is_stored_when_none_was_drawn():
    from app.memory.conversations import chart_for_storage

    assert chart_for_storage(None, None) is None
    assert chart_for_storage({**PANEL, "points": None}, "month") is None  # the panel failed
    assert chart_for_storage({**PANEL, "points": []}, "month") is None    # nothing to draw


def test_a_huge_chart_keeps_its_bars_and_drops_its_hover_rows():
    from app.memory.conversations import MAX_CHART_BYTES, chart_for_storage

    big = {**PANEL, "details": [{"subject": "x" * 300}] * (MAX_CHART_BYTES // 300 + 10)}
    stored = chart_for_storage(big, "week")
    assert stored["panel"]["points"] == PANEL["points"]
    assert stored["panel"]["details"] == []


def test_a_bad_stored_chart_is_not_drawn():
    from app.memory.conversations import chart_for_history

    assert chart_for_history("not json") is None
    assert chart_for_history({"panel": {"points": "no"}}) is None
    assert chart_for_history(None) is None
