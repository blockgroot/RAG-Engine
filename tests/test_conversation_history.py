"""Conversation history: scoping, retention, and the fold that stopped deleting.

The load-bearing change is that `set_summary_and_prune` became
`set_summary_folded_through` -- turns are MARKED as folded, not removed. The
summary is the model's working memory and the transcript is the person's
record; one table was doing both jobs and deletion was correct for only one of
them. `get_context` already bounded what the LLM sees, so nothing needed the
rows gone.
"""

from __future__ import annotations

import pytest

from app.memory import conversations as store


class _Conn:
    """Records the SQL a store function issues, returns nothing."""

    def __init__(self, rows=None):
        self.sql: list[str] = []
        self.params: list[tuple] = []
        self._rows = rows or []

    def execute(self, sql, params):
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def conn(monkeypatch):
    c = _Conn()
    monkeypatch.setattr(store, "get_connection", lambda: c)
    return c


# --- scoping: every read and the delete carry all three predicates -----------


def test_every_query_is_scoped_to_org_user_and_workspace(conn):
    """A missing predicate here returns (or deletes) someone else's chat."""
    kw = {"org_id": "o", "user_id": "u", "workspace_id": None}
    store.list_conversations(**kw)
    store.get_conversation_turns(conversation_id="c", **kw)
    store.delete_conversation(conversation_id="c", **kw)

    assert len(conn.sql) == 3
    for sql in conn.sql:
        assert "org_id = %s::uuid" in sql, sql
        assert "user_id = %s::uuid" in sql, sql
        assert "workspace_id IS NOT DISTINCT FROM %s::uuid" in sql, sql


def test_delete_reports_whether_anything_went(conn, monkeypatch):
    """False must become a 404, never a 403 -- a 403 confirms the id exists."""
    monkeypatch.setattr(store, "get_connection", lambda: _Conn(rows=[]))
    assert (
        store.delete_conversation(
            conversation_id="c", org_id="o", user_id="u", workspace_id=None
        )
        is False
    )
    monkeypatch.setattr(store, "get_connection", lambda: _Conn(rows=[(1,)]))
    assert (
        store.delete_conversation(
            conversation_id="c", org_id="o", user_id="u", workspace_id=None
        )
        is True
    )


# --- retention ---------------------------------------------------------------


def test_retention_is_keyed_on_last_activity_not_creation(conn):
    """A chat returned to yesterday is not 30 days old because it started then."""
    store.purge_expired_conversations(30)
    sql = conn.sql[0]
    assert "DELETE FROM conversations" in sql
    assert "max(t.created_at)" in sql, "must use the latest turn, not created_at alone"
    assert "make_interval(days => %s)" in sql
    assert conn.params[0] == (30,)


def test_there_is_no_display_cap_to_disagree_with_retention(conn):
    """The sweep IS the list length.

    A capped list plus a longer retention leaves a live chat off the end:
    unreachable and undeletable, the exact bug history was built to remove.
    CLAUDE.md records the same class for FREQUENCIES/_FREQUENCY_INTERVAL.
    """
    store.list_conversations(org_id="o", user_id="u", workspace_id=None)
    # Only the OUTER query matters -- the title subquery has its own LIMIT 1.
    tail = conn.sql[0].upper().split("ORDER BY LAST_ACTIVITY_AT DESC")[-1]
    assert "LIMIT" not in tail, f"outer query is capped: {tail!r}"


def test_empty_conversations_are_not_listed(conn):
    """`ensureConversation` creates a row before the first question, so an
    abandoned visit leaves a blank one. They still age out on the TTL."""
    store.list_conversations(org_id="o", user_id="u", workspace_id=None)
    assert "EXISTS (SELECT 1 FROM conversation_turns" in conn.sql[0]


# --- the fold keeps the transcript ------------------------------------------


def test_the_store_contract_no_longer_offers_a_prune():
    """A pruning method left on the interface is one a future caller can use."""
    from app.memory.base import ConversationStore

    assert not hasattr(ConversationStore, "set_summary_and_prune")
    assert hasattr(ConversationStore, "set_summary_folded_through")
    assert hasattr(ConversationStore, "get_folded_through")


def test_the_fold_marker_only_moves_forward():
    """Two overlapping folds must not rewind it and re-fold folded turns."""
    from tests.fakes import InMemoryConversationStore

    memory = InMemoryConversationStore()
    cid = memory.create_conversation("org")
    memory.set_summary_folded_through(cid, "s", 5)
    memory.set_summary_folded_through(cid, "s", 2)
    assert memory.get_folded_through(cid) == 5


def test_a_reopened_turn_returns_its_sources_and_drops_a_bad_link(monkeypatch):
    """The history payload is what the page draws. A non-http url must not survive."""
    from datetime import datetime, timezone

    raw = [
        {
            "n": 1,
            "document_id": "d-leave",
            "title": "Leave Policy",
            "provider": "notion",
            "url": "https://notion.so/leave",
        },
        {
            "n": 2,
            "document_id": "d-bad",
            "title": "Nope",
            "provider": "notion",
            "url": "javascript:alert(1)",
        },
        {"n": "1", "document_id": "d-x"},
    ]
    conn = _Conn(rows=[(0, "q", "Leave [1].", datetime.now(timezone.utc), raw, None, None)])
    monkeypatch.setattr(store, "get_connection", lambda: conn)
    turns = store.get_conversation_turns(
        conversation_id="c", org_id="o", user_id="u", workspace_id=None
    )
    assert "t.cited" in conn.sql[0]
    assert turns[0].cited == [
        {
            "n": 1,
            "document_id": "d-leave",
            "title": "Leave Policy",
            "provider": "notion",
            "url": "https://notion.so/leave",
        },
        {
            "n": 2,
            "document_id": "d-bad",
            "title": "Nope",
            "provider": "notion",
            "url": None,
        },
    ]


def test_append_turn_writes_the_citation_list(monkeypatch):
    """The column is the only reason a reopened chat can draw its sources."""
    from app.memory.pg_store import PgConversationStore

    class _Write:
        def __init__(self):
            self.sql: list[str] = []
            self.params: list[tuple] = []
            self._n = 0

        def execute(self, sql, params):
            self.sql.append(" ".join(sql.split()))
            self.params.append(params)
            self._n += 1
            return self

        def fetchone(self):
            return ("org",) if self._n == 1 else (0,)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    conn = _Write()
    monkeypatch.setattr("app.memory.pg_store.get_connection", lambda settings=None: conn)
    PgConversationStore().append_turn(
        "cid",
        "q",
        "Leave [1].",
        [{"n": 1, "document_id": "d-leave", "title": "Leave", "provider": "notion", "url": "https://notion.so/leave"}],
    )
    assert "cited" in conn.sql[-1]
    assert '"document_id": "d-leave"' in conn.params[-1][-2]
    assert conn.params[-1][-1] is None  # no chart drawn, none stored


def test_append_turn_writes_the_chart(monkeypatch):
    """Without the column a reopened chat shows the words and loses the chart."""
    import json

    from app.memory.conversations import chart_for_storage
    from app.memory.pg_store import PgConversationStore

    class _Write:
        def __init__(self):
            self.params: list[tuple] = []
            self._n = 0

        def execute(self, sql, params):
            self.params.append(params)
            self._n += 1
            return self

        def fetchone(self):
            return ("org",) if self._n == 1 else (0,)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    conn = _Write()
    monkeypatch.setattr("app.memory.pg_store.get_connection", lambda settings=None: conn)
    panel = {"chart": "bar", "points": [{"group": "High", "value": 4}]}
    PgConversationStore().append_turn("cid", "chart it", "Here.", None,
                                      chart_for_storage(panel, "month"))
    assert json.loads(conn.params[-1][-1]) == {"panel": panel, "period": "month"}


def test_a_reopened_turn_returns_its_chart(monkeypatch):
    from datetime import datetime, timezone

    stored = {"panel": {"chart": "bar", "points": [{"group": "High", "value": 4}]},
              "period": "week"}
    conn = _Conn(rows=[(0, "q", "a", datetime.now(timezone.utc), None, stored, None)])
    monkeypatch.setattr(store, "get_connection", lambda: conn)
    [turn] = store.get_conversation_turns(
        conversation_id="c", org_id="o", user_id="u", workspace_id=None
    )
    assert "t.chart" in conn.sql[0]
    assert turn.chart == stored


def test_a_reopened_turn_keeps_who_answered(monkeypatch):
    """The pill under an answer must survive a reload, not only the text."""
    from datetime import datetime, timezone

    meta = {"source": "notion", "agent": "notion", "connected_providers": ["notion", "slack"],
            "model": "Qwen 3.8 27B", "citation_count": 3}
    conn = _Conn(rows=[(0, "q", "a", datetime.now(timezone.utc), None, None, meta)])
    monkeypatch.setattr(store, "get_connection", lambda: conn)
    [turn] = store.get_conversation_turns(
        conversation_id="c", org_id="o", user_id="u", workspace_id=None
    )
    assert "t.meta" in conn.sql[0]
    assert turn.meta == meta


def test_the_stored_pill_keeps_counts_not_passages():
    payload = {"source": "notion", "agent": "notion", "answer": "x",
               "citations": [{"content": "secret passage", "reference": "r", "score": 1}] * 2,
               "attachments": [], "model": None, "live_sources": []}
    meta = store.meta_for_storage(payload)
    assert meta == {"source": "notion", "agent": "notion", "citation_count": 2}
    assert "secret passage" not in str(meta)


def test_a_bad_stored_pill_is_not_drawn():
    assert store.meta_for_history("not json") is None
    assert store.meta_for_history({"agent": "notion"}) is None  # no source
    assert store.meta_for_history(None) is None
