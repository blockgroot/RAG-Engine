"""Upkeep runs without a cron: the worker inside the API calls it while awake.

Render's free plan sleeps after 15 idle minutes, and keeping it awake with a
cron spent the month's instance hours, so the cron is gone. Polling, Drive
watch renewal and retention moved into ``run_upkeep``; the one-off backfills
(document facts, graph) stay on the cron path only.
"""

from __future__ import annotations

from app.jobs import worker


def _record(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(worker, "run_sync_tick", lambda: calls.append("sync") or 0)
    monkeypatch.setattr(worker, "run_facts_tick", lambda: calls.append("facts") or 0)
    monkeypatch.setattr(worker, "backfill_all_document_facts", lambda: calls.append("doc_backfill") or 0)
    import app.sources.drive_watch as drive_watch
    import app.attachments as attachments
    import app.memory.conversations as conversations
    import app.livetools.audit as audit
    import app.guard.backfill as guard_backfill
    import app.graph.builder as graph_builder

    monkeypatch.setattr(drive_watch, "ensure_watches", lambda: calls.append("drive") or 0)
    monkeypatch.setattr(attachments, "purge_expired_attachments", lambda: calls.append("attachments") or 0)
    monkeypatch.setattr(conversations, "purge_expired_conversations", lambda: calls.append("chats") or 0)
    monkeypatch.setattr(audit, "purge_expired", lambda: calls.append("audit") or 0)
    monkeypatch.setattr(guard_backfill, "backfill_injection_scores", lambda: calls.append("injection") or 0)
    monkeypatch.setattr(graph_builder, "backfill", lambda: calls.append("graph") or 0)
    import app.api.slack_events as slack_events

    monkeypatch.setattr(slack_events, "purge_event_receipts", lambda: calls.append("slack_receipts") or 0)
    return calls


def test_upkeep_does_everything_but_the_backfills(monkeypatch):
    calls = _record(monkeypatch)
    out = worker.run_upkeep()
    assert calls == ["sync", "facts", "drive", "attachments", "chats", "audit", "injection", "slack_receipts"]
    assert set(out) == {
        "syncs_queued", "facts_recorded", "drive_watches_opened",
        "attachments_purged", "conversations_purged", "injection_scored",
    }


def test_the_cron_tick_still_does_upkeep_and_the_backfills(monkeypatch):
    calls = _record(monkeypatch)
    monkeypatch.setattr(worker.queue, "reap_stuck", lambda: 0)
    monkeypatch.setattr(worker, "run_scheduler_tick", lambda settings=None: 0)
    out = worker.run_external_tick()
    assert {"doc_backfill", "graph", "drive", "chats"} <= set(calls)
    assert {"reaped", "syncs_queued", "facts_backfilled", "schedulers_ran"} <= set(out)


def test_one_failing_step_never_stops_the_rest(monkeypatch):
    calls = _record(monkeypatch)
    import app.sources.drive_watch as drive_watch

    def broken():
        raise RuntimeError("Google is down")

    monkeypatch.setattr(drive_watch, "ensure_watches", broken)
    worker.run_upkeep()
    assert "chats" in calls and "injection" in calls
