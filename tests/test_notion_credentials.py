"""Per-organization Notion credential tests (Phase 9, Part 2).

Prove that an ingestion run uses the SPECIFIC token it was pointed at and never
silently falls back to another org's token or a stale global one — the property
that makes each org a real, Notion-enforced access boundary. No network: the
Notion ``Client`` is monkeypatched to record the auth it was constructed with.
"""

from __future__ import annotations

import os

import pytest

from app.config.settings import NotionSettings
from app.core.exceptions import ConfigurationError
from app.sources import build_source_adapter
from app.sources.notion import NotionAdapter


@pytest.fixture(autouse=True)
def _fake_notion_client(monkeypatch):
    """Replace notion_client.Client so building an adapter records auth, no network."""
    import notion_client

    class FakeClient:
        def __init__(self, auth):
            self.auth = auth

    monkeypatch.setattr(notion_client, "Client", FakeClient)


@pytest.fixture
def _tokens(monkeypatch):
    """A default token plus two distinct per-org tokens.

    Clears any real NOTION_TOKEN_* already in the environment (e.g. from a
    developer's .env used for live ingestion) so this test's discovery assertion
    isn't polluted by unrelated real tokens.
    """
    for key in list(os.environ):
        if key.startswith("NOTION_TOKEN_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NOTION_TOKEN", "GLOBAL-DEFAULT")
    monkeypatch.setenv("NOTION_TOKEN_ACME", "tok-acme")
    monkeypatch.setenv("NOTION_TOKEN_GLOBEX", "tok-globex")


def test_from_env_discovers_named_tokens_generically(_tokens):
    settings = NotionSettings.from_env()
    assert settings.tokens == {"acme": "tok-acme", "globex": "tok-globex"}
    assert settings.token == "GLOBAL-DEFAULT"


def test_resolve_token_returns_the_named_org_token(_tokens):
    settings = NotionSettings.from_env()
    assert settings.resolve_token("acme") == "tok-acme"
    assert settings.resolve_token("GLOBEX") == "tok-globex"  # case-insensitive
    assert settings.resolve_token(None) == "GLOBAL-DEFAULT"  # default only when unnamed


def test_resolve_token_never_falls_back_for_a_named_but_missing_org(_tokens):
    settings = NotionSettings.from_env()
    with pytest.raises(ConfigurationError) as exc:
        settings.resolve_token("initech")  # not configured
    # It must error, not quietly hand back GLOBAL-DEFAULT or another org's token.
    assert "initech" in str(exc.value).lower()


def test_adapter_authenticates_with_the_specific_org_token(_tokens):
    # Build the adapter exactly as an ingestion run would, for a specific org.
    adapter = build_source_adapter("notion", token_name="acme")
    assert adapter._client.auth == "tok-acme"
    # Crucially NOT the global default nor the other org's token.
    assert adapter._client.auth != "GLOBAL-DEFAULT"
    assert adapter._client.auth != "tok-globex"


def test_adapter_explicit_token_overrides_settings_default(_tokens):
    # A directly-passed token wins over the settings' default token.
    adapter = NotionAdapter(settings=NotionSettings.from_env(), token="tok-globex")
    assert adapter._client.auth == "tok-globex"


def test_no_token_at_all_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    settings = NotionSettings(token=None, tokens={})
    with pytest.raises(ConfigurationError):
        settings.resolve_token(None)
