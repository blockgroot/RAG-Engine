"""A Slack question whose process died mid-answer is answered again, once,
never while its first answer is still running; its text is kept only until done."""

from __future__ import annotations

import time
import uuid

import pytest

from app.api import slack_events
from app.db.connection import get_connection

from .conftest import requires_db


def _receipt(event_id: str, *, alive: str, received: str, attempts: int = 1) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO slack_event_receipts (event_id, status, team_id, event, alive_at, "
            "received_at, attempts) VALUES (%s, 'pending', 'T1', '{\"text\": \"q\"}'::jsonb, "
            f"now() - interval '{alive}', now() - interval '{received}', %s)",
            (event_id, attempts),
        )


def _row(event_id: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT status, event IS NULL, attempts FROM slack_event_receipts WHERE event_id = %s",
            (event_id,),
        ).fetchone()


@requires_db
def test_lost_questions_are_replayed_live_ones_are_not_and_old_ones_given_up(monkeypatch):
    with get_connection() as conn:
        foreign = conn.execute(
            "SELECT count(*) FROM slack_event_receipts WHERE status = 'pending'").fetchone()[0]
    if foreign:
        pytest.skip("real pending Slack questions in this database; replay would take them")

    tag = uuid.uuid4().hex[:8]
    dead, live, old, spent = (f"test-{tag}-{n}" for n in ("dead", "live", "old", "spent"))
    _receipt(dead, alive="5 minutes", received="5 minutes")
    _receipt(live, alive="10 seconds", received="5 minutes")
    _receipt(old, alive="2 hours", received="2 hours")
    _receipt(spent, alive="5 minutes", received="5 minutes", attempts=3)
    replayed: list = []
    monkeypatch.setattr(slack_events, "_answer_durably",
                        lambda event_id, event, team, **k: replayed.append((event_id, event, k)))
    try:
        assert slack_events.replay_lost_questions() == 1
        time.sleep(0.2)  # the replay runs on its own thread
        assert replayed == [(dead, {"text": "q"}, {"waited": True})]
        assert _row(dead) == ("pending", False, 2)
        assert _row(live) == ("pending", False, 1)
        assert _row(old) == ("done", True, 1)       # past the window: text cleared
        assert _row(spent) == ("done", True, 3)     # past the attempt cap
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM slack_event_receipts WHERE event_id LIKE %s",
                         (f"test-{tag}-%",))


@requires_db
def test_an_answered_question_is_closed_and_its_text_cleared(monkeypatch):
    event_id = f"test-{uuid.uuid4().hex[:8]}-closed"
    assert slack_events._first_delivery(event_id, "T1", {"text": "q"}) is True
    assert _row(event_id) == ("pending", False, 1)
    monkeypatch.setattr(slack_events, "_handle", lambda *a, **k: None)
    try:
        slack_events._answer_durably(event_id, {"text": "q"}, "T1", waited=False)
        assert _row(event_id) == ("done", True, 1)
        assert slack_events._first_delivery(event_id, "T1", {"text": "q"}) is False
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM slack_event_receipts WHERE event_id = %s", (event_id,))


@requires_db
def test_the_notion_token_is_stored_encrypted_first_one_kept_and_cleared():
    from app.api import webhooks

    if webhooks.stored_notion_token() is not None:
        pytest.skip("this database holds a real Notion token; never touch it")
    try:
        assert webhooks.store_notion_token("secret_one") is True
        assert webhooks.store_notion_token("secret_two") is False
        assert webhooks.stored_notion_token() == "secret_one"
        with get_connection() as conn:
            raw = conn.execute(
                "SELECT value_encrypted FROM deployment_secrets WHERE name = %s",
                (webhooks._NOTION_TOKEN,)).fetchone()[0]
        assert "secret_one" not in raw
    finally:
        webhooks.forget_notion_token()
    assert webhooks.stored_notion_token() is None
