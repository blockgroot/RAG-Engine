"""Ingestion worker memory admission gate.

Defense-in-depth alongside the per-input bounds fixed elsewhere (Notion fetch
size cap, remote-embed batching, contextual chunk-count cap): those close
specific known holes; this is a general circuit breaker that declines to
claim new work when the process is already close to its memory ceiling,
catching pressure from any cause -- including ones not found by hand yet.

No DB, no network: ``queue.claim_next`` is monkeypatched with a spy so we can
prove the gate short-circuits BEFORE any DB interaction.
"""

from __future__ import annotations

from app.config.settings import IngestWorkerSettings
from app.jobs import worker


def test_run_once_skips_claiming_when_rss_is_over_the_ceiling(monkeypatch):
    monkeypatch.setattr(worker, "_current_rss_mb", lambda: 450.0)
    settings = IngestWorkerSettings(memory_guard_enabled=True, max_rss_mb=400.0)
    monkeypatch.setattr(
        "app.jobs.worker.IngestWorkerSettings.from_env", lambda: settings
    )

    claimed = []
    monkeypatch.setattr(worker.queue, "claim_next", lambda: claimed.append(1) or None)

    result = worker.run_once()

    assert result is None
    assert claimed == [], "must not touch the queue at all once over the ceiling"


def test_run_once_proceeds_normally_when_rss_is_comfortably_under_ceiling(monkeypatch):
    monkeypatch.setattr(worker, "_current_rss_mb", lambda: 100.0)
    settings = IngestWorkerSettings(memory_guard_enabled=True, max_rss_mb=400.0)
    monkeypatch.setattr(
        "app.jobs.worker.IngestWorkerSettings.from_env", lambda: settings
    )

    claimed = []
    monkeypatch.setattr(
        worker.queue, "claim_next", lambda: claimed.append(1) or None
    )

    result = worker.run_once()

    assert result is None  # empty queue in this fake, but claim_next WAS called
    assert claimed == [1], "must still attempt to claim when under the ceiling"


def test_guard_disabled_always_proceeds_regardless_of_rss(monkeypatch):
    monkeypatch.setattr(worker, "_current_rss_mb", lambda: 10_000.0)
    settings = IngestWorkerSettings(memory_guard_enabled=False, max_rss_mb=400.0)
    monkeypatch.setattr(
        "app.jobs.worker.IngestWorkerSettings.from_env", lambda: settings
    )

    claimed = []
    monkeypatch.setattr(worker.queue, "claim_next", lambda: claimed.append(1) or None)

    worker.run_once()

    assert claimed == [1], "kill-switch must fully bypass the RSS check"


def test_env_wiring_reads_the_configured_ceiling(monkeypatch):
    monkeypatch.setenv("INGEST_MEMORY_GUARD_ENABLED", "true")
    monkeypatch.setenv("INGEST_MAX_RSS_MB", "123")

    settings = IngestWorkerSettings.from_env()

    assert settings.memory_guard_enabled is True
    assert settings.max_rss_mb == 123.0


def test_current_rss_mb_returns_a_positive_number():
    """Smoke test against the real resource module -- no mocking, just proves
    the platform-dependent unit conversion doesn't crash and returns something
    sane on whatever OS the tests actually run on."""
    assert worker._current_rss_mb() > 0
