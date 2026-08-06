"""GitHub installation tokens through the credentials layer (Plan Phase 3).

``get_live_connection_token`` is the one place that owns "give me a usable token
for this connection". GitHub is the first provider where the token handed to
callers is **not** the stored one: the stored token is the *user* token (proof of
who connected), while repo reads need a short-lived *installation* token minted
from the App private key. That substitution belongs here rather than in the
reader, so every caller benefits without knowing which provider it holds — the
same reasoning that put Google's refresh here (CLAUDE.md D10).

These tests also pin the in-process cache: minting on every question would burn
rate limit and add latency for no benefit, since a token is valid for an hour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import OAuthTokens, save_connection, set_connection_config
from app.auth.credentials import get_live_connection_token
from app.core.exceptions import ConfigurationError
from app.security import encrypt  # noqa: F401  (ensures the module imports cleanly)

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _clear_installation_cache():
    """The cache is process-global; isolate every test from its neighbours."""
    from app.auth import credentials

    credentials._INSTALLATION_TOKEN_CACHE.clear()
    yield
    credentials._INSTALLATION_TOKEN_CACHE.clear()


def _connect_github(store, org_cleanup, name: str, *, with_installation: bool = True) -> str:
    org_id = store.create_organization(name)
    org_cleanup.append(org_id)
    save_connection(
        org_id,
        "github",
        OAuthTokens(
            access_token="ghu_user_token",
            refresh_token=None,
            # A user token's own expiry must not drive installation minting.
            expires_at=None,
            external_workspace_id="acme-inc",
        ),
    )
    if with_installation:
        set_connection_config(
            org_id, "github", {"installation_id": "4242", "account_login": "acme-inc"}
        )
    return org_id


def _fake_mint(monkeypatch, calls: list, *, token: str = "ghs_minted", ttl_minutes: int = 60):
    from app.auth.github_app import InstallationToken

    def _mint(installation_id, settings=None):
        calls.append(installation_id)
        return InstallationToken(
            token=token,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
        )

    monkeypatch.setattr("app.auth.github_app.mint_installation_token", _mint)


@requires_db
def test_github_returns_a_minted_installation_token_not_the_stored_user_token(
    store, org_cleanup, monkeypatch
):
    org_id = _connect_github(store, org_cleanup, "GH Mint Org")
    calls: list = []
    _fake_mint(monkeypatch, calls)

    token = get_live_connection_token(org_id, "github")

    assert token == "ghs_minted"
    assert token != "ghu_user_token"
    assert calls == ["4242"]


@requires_db
def test_second_call_within_validity_window_uses_the_cache(store, org_cleanup, monkeypatch):
    org_id = _connect_github(store, org_cleanup, "GH Cache Org")
    calls: list = []
    _fake_mint(monkeypatch, calls)

    first = get_live_connection_token(org_id, "github")
    second = get_live_connection_token(org_id, "github")

    assert first == second == "ghs_minted"
    assert calls == ["4242"], "a cached, still-valid token must not be re-minted"


@requires_db
def test_expiring_cached_token_is_reminted(store, org_cleanup, monkeypatch):
    org_id = _connect_github(store, org_cleanup, "GH Remint Org")
    calls: list = []
    # Inside the 5-minute safety margin, so it must not be reused.
    _fake_mint(monkeypatch, calls, token="ghs_first", ttl_minutes=2)

    get_live_connection_token(org_id, "github")
    _fake_mint(monkeypatch, calls, token="ghs_second", ttl_minutes=60)
    second = get_live_connection_token(org_id, "github")

    assert second == "ghs_second"
    assert calls == ["4242", "4242"]


@requires_db
def test_missing_installation_id_raises_an_actionable_error(store, org_cleanup, monkeypatch):
    """A GitHub row with no installation id can never mint — say so clearly."""
    org_id = _connect_github(store, org_cleanup, "GH No Install Org", with_installation=False)
    calls: list = []
    _fake_mint(monkeypatch, calls)

    with pytest.raises(ConfigurationError) as excinfo:
        get_live_connection_token(org_id, "github")

    assert "reconnect" in str(excinfo.value).lower()
    assert calls == [], "must fail before attempting to mint"


@requires_db
def test_cache_is_keyed_per_org_so_tokens_never_cross_tenants(store, org_cleanup, monkeypatch):
    """Two orgs, two installations: neither may receive the other's token."""
    org_a = _connect_github(store, org_cleanup, "GH Tenant A")
    org_b = store.create_organization("GH Tenant B")
    org_cleanup.append(org_b)
    save_connection(
        org_b,
        "github",
        OAuthTokens(
            access_token="ghu_b",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="globex",
        ),
    )
    set_connection_config(
        org_b, "github", {"installation_id": "9999", "account_login": "globex"}
    )

    from app.auth.github_app import InstallationToken

    def _mint(installation_id, settings=None):
        return InstallationToken(
            token=f"ghs_for_{installation_id}",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=60),
        )

    monkeypatch.setattr("app.auth.github_app.mint_installation_token", _mint)

    token_a = get_live_connection_token(org_a, "github")
    token_b = get_live_connection_token(org_b, "github")

    assert token_a == "ghs_for_4242"
    assert token_b == "ghs_for_9999"


@requires_db
def test_notion_behaviour_is_unchanged(store, org_cleanup, monkeypatch):
    """Regression: the GitHub branch must not alter any other provider's path."""
    org_id = store.create_organization("GH Regression Notion Org")
    org_cleanup.append(org_id)
    save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_stored",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-1",
        ),
    )

    def _explode(installation_id, settings=None):  # pragma: no cover
        raise AssertionError("Notion must never mint a GitHub installation token")

    monkeypatch.setattr("app.auth.github_app.mint_installation_token", _explode)

    assert get_live_connection_token(org_id, "notion") == "ntn_stored"
