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
    """Smoke test against the real platform -- no mocking, just proves the
    /proc-or-ps unit conversion doesn't crash and returns something sane on
    whatever OS the tests actually run on."""
    assert worker._current_rss_mb() > 0


def test_current_rss_reflects_freed_memory_not_a_high_water_mark():
    """Regression: the guard originally used ``resource.getrusage().ru_maxrss``,
    which is a MONOTONIC PEAK that never decreases once memory is freed. That
    made the gate latch permanently closed after any transient spike, silently
    disabling ingestion for the life of the process instead of throttling it.

    Allocate a large block, free it, and assert the reading comes back down.
    """
    import gc

    baseline = worker._current_rss_mb()
    block = bytearray(200 * 1024 * 1024)
    peak = worker._current_rss_mb()
    assert peak > baseline + 100, "allocation should be observable"

    del block
    gc.collect()
    after_free = worker._current_rss_mb()

    assert after_free < peak - 50, (
        f"RSS did not fall after freeing 200MB (peak={peak:.0f}MB, "
        f"after_free={after_free:.0f}MB) -- this is the ru_maxrss "
        "high-water-mark bug that permanently disabled ingestion"
    )


def test_a_broken_rss_reading_fails_open_rather_than_blocking_all_work(monkeypatch):
    """A measurement failure must not be able to stop ingestion entirely."""
    monkeypatch.setattr(worker, "_current_rss_mb", lambda: 0.0)
    settings = IngestWorkerSettings(memory_guard_enabled=True, max_rss_mb=400.0)
    monkeypatch.setattr(
        "app.jobs.worker.IngestWorkerSettings.from_env", lambda: settings
    )
    claimed = []
    monkeypatch.setattr(worker.queue, "claim_next", lambda: claimed.append(1) or None)

    worker.run_once()

    assert claimed == [1], "unknown memory must fail open, not closed"
