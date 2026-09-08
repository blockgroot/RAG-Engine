"""Disconnecting a source must leave nothing behind that can still answer."""

from __future__ import annotations

import pytest

from app.api import connection_ops
from app.sources.factory import INDEXED_PROVIDERS


class _Store:
    def __init__(self):
        self.deleted: list[tuple] = []

    def delete_all_source_documents(self, org_id, provider, workspace_id=None):
        self.deleted.append((org_id, provider, workspace_id))
        return 7


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr(connection_ops, "build_vector_store", lambda: s)
    return s


@pytest.mark.parametrize("provider", INDEXED_PROVIDERS)
def test_every_ingesting_provider_is_purged(store, provider):
    # Linear was missing from the hand-kept copy of this list, so a
    # disconnected Linear kept every issue indexed -- and kept answering.
    assert connection_ops.purge_provider_documents("org", provider) == 7
    assert store.deleted == [("org", provider, None)]


def test_github_has_nothing_to_purge(store):
    # Live-only: disconnect drops the oauth row and nothing else exists.
    assert connection_ops.purge_provider_documents("org", "github") == 0
    assert store.deleted == []


def test_disconnect_purges_and_clears_the_answer_cache(monkeypatch, store):
    cleared: list[str] = []
    monkeypatch.setattr(connection_ops, "delete_connection", lambda *a, **k: "linear")
    monkeypatch.setattr(connection_ops, "delete_org_entries", lambda org: cleared.append(org))

    result = connection_ops.disconnect_connection("org", "conn")

    assert result == {"provider": "linear", "documents_purged": 7}
    assert store.deleted == [("org", "linear", None)]
    # Otherwise a cached answer built from the disconnected source is served
    # for up to the cache TTL.
    assert cleared == ["org"]


def test_a_cache_failure_never_fails_the_disconnect(monkeypatch, store):
    monkeypatch.setattr(connection_ops, "delete_connection", lambda *a, **k: "notion")

    def boom(_org):
        raise RuntimeError("cache down")

    monkeypatch.setattr(connection_ops, "delete_org_entries", boom)
    assert connection_ops.disconnect_connection("org", "conn")["provider"] == "notion"


def test_indexed_providers_matches_the_adapters_that_exist():
    # The guard against this drifting again: every provider the factory can
    # build writes documents, so every one of them must be purgeable.
    from app.sources import factory

    for provider in INDEXED_PROVIDERS:
        assert hasattr(factory, "build_source_adapter")
    assert "github" not in INDEXED_PROVIDERS
