"""Phase 2 (Google integration plan): provider-agnostic OAuth token refresh.

``get_live_connection_token`` is the one place that decides whether a stored
access token is still good and, if not, refreshes it — generically, so every
caller (job worker, admin API) benefits without knowing which provider it's
talking to. Notion never expires tokens; these tests use a **fake**
``OAuthProvider`` (Google's real provider lands in a parallel phase) so this
logic is proven independently of it, per the established idiom in
``tests/test_auth.py`` of monkeypatching at the module boundary the code
under test actually calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth import OAuthTokens, get_connection_token, get_live_connection_token, save_connection
from app.core.exceptions import OAuthReauthRequiredError
from app.db.connection import get_connection

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_encryption_key(monkeypatch):
    """A real Fernet key so encrypt/decrypt work end to end in these tests."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


class _FakeOAuthProvider:
    """A minimal fake standing in for a not-yet-built ``GoogleOAuthProvider``."""

    def __init__(self, refresh_fn):
        self._refresh_fn = refresh_fn

    def authorize_url(self, state):  # pragma: no cover - unused here
        raise NotImplementedError

    def exchange_code(self, code):  # pragma: no cover - unused here
        raise NotImplementedError

    def refresh(self, refresh_token):
        return self._refresh_fn(refresh_token)


class _NoRefreshOAuthProvider:
    """Stands in for a provider that doesn't implement refresh (e.g. Notion)."""

    def refresh(self, refresh_token):
        raise NotImplementedError("does not support token refresh")


def _register_fake_provider(monkeypatch, provider_instance):
    # ``get_live_connection_token`` does a lazy ``from .factory import
    # build_oauth_provider`` inside the function body (see credentials.py),
    # so there's no persistent ``app.auth.credentials.build_oauth_provider``
    # attribute to patch — patch the factory function itself instead; the
    # lazy import re-resolves it fresh on every call.
    monkeypatch.setattr(
        "app.auth.factory.build_oauth_provider",
        lambda provider: provider_instance,
    )


@requires_db
def test_expired_token_is_refreshed_and_persisted(store, org_cleanup, monkeypatch):
    org_id = store.create_organization("Token Refresh Expired Org")
    org_cleanup.append(org_id)

    save_connection(
        org_id,
        "google",
        OAuthTokens(
            access_token="old-access",
            refresh_token="old-refresh",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            external_workspace_id="user@example.com",
        ),
    )

    calls = []

    def fake_refresh(refresh_token):
        calls.append(refresh_token)
        return OAuthTokens(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            external_workspace_id="user@example.com",
        )

    _register_fake_provider(monkeypatch, _FakeOAuthProvider(fake_refresh))

    token = get_live_connection_token(org_id, "google")

    assert token == "new-access"
    assert calls == ["old-refresh"]
    # Persisted: a follow-up read (raw accessor) sees the new access token.
    assert get_connection_token(org_id, "google") == "new-access"


@requires_db
def test_near_expiry_token_within_safety_margin_is_refreshed(store, org_cleanup, monkeypatch):
    org_id = store.create_organization("Token Refresh Near Expiry Org")
    org_cleanup.append(org_id)

    save_connection(
        org_id,
        "google",
        OAuthTokens(
            access_token="old-access",
            refresh_token="old-refresh",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
            external_workspace_id="user@example.com",
        ),
    )

    calls = []

    def fake_refresh(refresh_token):
        calls.append(refresh_token)
        return OAuthTokens(
            access_token="new-access",
            refresh_token=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            external_workspace_id="user@example.com",
        )

    _register_fake_provider(monkeypatch, _FakeOAuthProvider(fake_refresh))

    token = get_live_connection_token(org_id, "google")

    assert token == "new-access"
    assert len(calls) == 1


@requires_db
def test_token_comfortably_valid_does_not_trigger_refresh(store, org_cleanup, monkeypatch):
    org_id = store.create_organization("Token Refresh Valid Org")
    org_cleanup.append(org_id)

    save_connection(
        org_id,
        "google",
        OAuthTokens(
            access_token="still-good-access",
            refresh_token="still-good-refresh",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            external_workspace_id="user@example.com",
        ),
    )

    calls = []

    def fake_refresh(refresh_token):
        calls.append(refresh_token)
        raise AssertionError("refresh should not have been called")

    _register_fake_provider(monkeypatch, _FakeOAuthProvider(fake_refresh))

    token = get_live_connection_token(org_id, "google")

    assert token == "still-good-access"
    assert calls == []  # never invoked


@requires_db
def test_null_expires_at_returns_stored_token_unchanged(store, org_cleanup, monkeypatch):
    org_id = store.create_organization("Token Refresh Notion-Shaped Org")
    org_cleanup.append(org_id)

    save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_secret",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-1",
        ),
    )

    def fake_refresh(refresh_token):
        raise AssertionError("refresh should not have been called for a null expires_at")

    _register_fake_provider(monkeypatch, _FakeOAuthProvider(fake_refresh))

    token = get_live_connection_token(org_id, "notion")

    assert token == "ntn_secret"


@requires_db
def test_provider_without_refresh_support_falls_back_to_stored_token(store, org_cleanup, monkeypatch):
    org_id = store.create_organization("Token Refresh NotImplemented Org")
    org_cleanup.append(org_id)

    save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_secret_expiring",
            refresh_token="ntn_refresh_unused",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            external_workspace_id="ws-1",
        ),
    )

    _register_fake_provider(monkeypatch, _NoRefreshOAuthProvider())

    # Must not propagate NotImplementedError to the caller.
    token = get_live_connection_token(org_id, "notion")
    assert token == "ntn_secret_expiring"


@requires_db
def test_terminal_refresh_failure_raises_reauth_required_and_does_not_retry(
    store, org_cleanup, monkeypatch
):
    org_id = store.create_organization("Token Refresh Terminal Failure Org")
    org_cleanup.append(org_id)

    save_connection(
        org_id,
        "google",
        OAuthTokens(
            access_token="old-access",
            refresh_token="revoked-refresh",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            external_workspace_id="user@example.com",
        ),
    )

    calls = []

    def fake_refresh(refresh_token):
        calls.append(refresh_token)
        raise RuntimeError("invalid_grant: token has been revoked")

    _register_fake_provider(monkeypatch, _FakeOAuthProvider(fake_refresh))

    with pytest.raises(OAuthReauthRequiredError):
        get_live_connection_token(org_id, "google")

    assert len(calls) == 1  # exactly one attempt, no retry loop

    # The stored token is left as-is (no partial/garbage write on failure).
    assert get_connection_token(org_id, "google") == "old-access"


@requires_db
def test_refresh_response_without_new_refresh_token_keeps_previous_one(
    store, org_cleanup, monkeypatch
):
    org_id = store.create_organization("Token Refresh Keeps Refresh Token Org")
    org_cleanup.append(org_id)

    save_connection(
        org_id,
        "google",
        OAuthTokens(
            access_token="old-access",
            refresh_token="original-refresh-token",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            external_workspace_id="user@example.com",
        ),
    )

    def fake_refresh(refresh_token):
        # Google frequently omits refresh_token on non-first refreshes.
        return OAuthTokens(
            access_token="rotated-access",
            refresh_token=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            external_workspace_id="user@example.com",
        )

    _register_fake_provider(monkeypatch, _FakeOAuthProvider(fake_refresh))

    token = get_live_connection_token(org_id, "google")
    assert token == "rotated-access"

    # The refresh response omitted a new refresh_token — confirm the ORIGINAL
    # one is still on file (not nulled out) by forcing another refresh cycle
    # and checking what gets passed to it.
    with get_connection() as conn:
        conn.execute(
            "UPDATE oauth_connections SET expires_at = %s "
            "WHERE org_id = %s AND provider = %s",
            (datetime.now(timezone.utc) - timedelta(minutes=1), org_id, "google"),
        )

    second_calls = []

    def fake_refresh_2(refresh_token):
        second_calls.append(refresh_token)
        return OAuthTokens(
            access_token="rotated-access-2",
            refresh_token=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            external_workspace_id="user@example.com",
        )

    _register_fake_provider(monkeypatch, _FakeOAuthProvider(fake_refresh_2))
    get_live_connection_token(org_id, "google")

    assert second_calls == ["original-refresh-token"]


from app.db.connection import get_connection  # noqa: E402  (kept near use above for clarity)
