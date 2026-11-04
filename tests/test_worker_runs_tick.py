"""The standalone worker runs the tick's jobs itself.

Drive watch renewal, backfills and expiry purges live only in
``run_external_tick``. A self-hosted install has no cron calling
``/internal/tick``, so if the worker loop stopped calling it, Drive pushes
would lapse within a week and expired chats would never be deleted.
"""

from __future__ import annotations

import pytest

from app.jobs import worker


class _Stop(Exception):
    pass


def test_the_worker_loop_runs_the_external_tick(monkeypatch):
    calls = []
    monkeypatch.setattr(worker.queue, "requeue_interrupted_running", lambda: 0)
    monkeypatch.setattr(worker.queue, "reap_stuck", lambda: 0)
    monkeypatch.setattr(
        "app.jobs.scheduler_queue.requeue_interrupted_running", lambda **_: 0
    )
    for name in (
        "start_cooled_down_pushes",
        "run_maintenance",
        "run_sync_tick",
        "run_facts_tick",
        "run_scheduler_tick",
    ):
        monkeypatch.setattr(worker, name, lambda *a, **k: 0)
    monkeypatch.setattr(worker, "run_external_tick", lambda: calls.append("tick"))

    def _stop():
        raise _Stop

    monkeypatch.setattr(worker, "run_once", _stop)

    with pytest.raises(_Stop):
        worker.run_forever()

    assert calls == ["tick"]
