"""Phase 3 (Slack Integration Plan, decision D11): per-provider contextual gate.

Pure unit test — ``_contextual_settings_for`` has no DB/network dependency.
"""

from __future__ import annotations

from app.config.settings import ContextualSettings
from app.jobs.worker import _contextual_settings_for


def test_slack_defaults_to_contextual_disabled(monkeypatch):
    monkeypatch.setenv("INGEST_CONTEXTUAL_ENABLED", "true")
    monkeypatch.delenv("SLACK_CONTEXTUAL_ENABLED", raising=False)

    settings = _contextual_settings_for("slack")

    assert settings.enabled is False


def test_slack_contextual_can_be_explicitly_re_enabled(monkeypatch):
    monkeypatch.setenv("INGEST_CONTEXTUAL_ENABLED", "true")
    monkeypatch.setenv("SLACK_CONTEXTUAL_ENABLED", "true")

    settings = _contextual_settings_for("slack")

    assert settings.enabled is True


def test_notion_and_google_are_unaffected_by_the_slack_gate(monkeypatch):
    monkeypatch.setenv("INGEST_CONTEXTUAL_ENABLED", "true")
    monkeypatch.delenv("SLACK_CONTEXTUAL_ENABLED", raising=False)

    for provider in ("notion", "google", None):
        settings = _contextual_settings_for(provider)
        assert settings.enabled is True


def test_globally_disabled_contextual_stays_disabled_for_slack(monkeypatch):
    monkeypatch.setenv("INGEST_CONTEXTUAL_ENABLED", "false")
    monkeypatch.setenv("SLACK_CONTEXTUAL_ENABLED", "true")

    # An explicit per-provider opt-in cannot resurrect a globally-off setting —
    # _contextual_settings_for only ever narrows the global config, never widens it.
    settings = _contextual_settings_for("slack")

    assert settings.enabled is False


def test_preserves_other_contextual_settings_fields(monkeypatch):
    monkeypatch.setenv("INGEST_CONTEXTUAL_ENABLED", "true")
    monkeypatch.setenv("INGEST_CONTEXTUAL_CONCURRENCY", "4")
    monkeypatch.setenv("INGEST_CONTEXTUAL_MAX_CHUNKS", "150")
    monkeypatch.delenv("SLACK_CONTEXTUAL_ENABLED", raising=False)

    settings = _contextual_settings_for("slack")
    baseline = ContextualSettings.from_env()

    assert settings.enabled is False
    assert settings.defer == baseline.defer
    assert settings.concurrency == baseline.concurrency == 4
    assert settings.max_chunks == baseline.max_chunks == 150
