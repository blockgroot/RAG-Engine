"""The GitHub App connect flow (GitHub Integration Plan, Phase 2).

Offline: ``httpx`` is monkeypatched at the module path, the same idiom as
``tests/test_auth.py`` / ``tests/test_google_oauth.py``.

The security-critical case here is
``test_exchange_rejects_installation_id_not_owned_by_the_user``. GitHub's own
docs warn that "bad actors can hit this URL with a spoofed installation_id",
and recommend verifying it with a user access token. Without that check, an
attacker could bind **someone else's** GitHub organization to their own tenant
and read its repos through our platform — a cross-tenant data leak. That test
is the regression guard for it and must never be weakened.
"""

from __future__ import annotations

import pytest

from app.auth.factory import build_oauth_provider
from app.auth.github_oauth import GitHubAppProvider
from app.config.settings import GitHubSettings
from app.core.exceptions import ConfigurationError, OAuthError


def _settings() -> GitHubSettings:
    return GitHubSettings(
        app_slug="acme-rag",
        client_id="Iv1.abc123",
        client_secret="s3cret",
        private_key="-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----",
    )


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


@pytest.fixture
def github_env(monkeypatch):
    """Populate the env so ``GitHubSettings.from_env()`` is fully configured."""
    monkeypatch.setenv("GITHUB_APP_SLUG", "acme-rag")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.abc123")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----\\nk\\n-----END PRIVATE KEY-----")


# -- authorize_url ---------------------------------------------------------


def test_authorize_url_starts_with_user_oauth_so_reconnect_works():
    """Already-installed Apps never complete via /installations/new alone.

    User OAuth always returns to our callback; if no installation exists yet,
    the callback redirects to ``install_url``.
    """
    provider = GitHubAppProvider(_settings())
    url = provider.authorize_url("st4te")

    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=Iv1.abc123" in url
    assert "state=st4te" in url
    assert provider.install_url("st4te").startswith(
        "https://github.com/apps/acme-rag/installations/new"
    )


def test_provider_requires_full_configuration():
    with pytest.raises(ConfigurationError):
        GitHubAppProvider(GitHubSettings(app_slug=None, client_id="x", client_secret="y"))
    with pytest.raises(ConfigurationError):
        GitHubAppProvider(GitHubSettings(app_slug="s", client_id=None, client_secret="y"))


# -- exchange --------------------------------------------------------------


def _wire(monkeypatch, *, token_payload, installations_payload):
    """Fake the two HTTP calls the exchange makes, recording what was sent."""
    seen: dict = {}

    def _fake_post(url, data=None, headers=None, timeout=None):
        seen["post_url"] = url
        seen["post_data"] = data
        seen["post_headers"] = headers or {}
        return _FakeResponse(token_payload)

    def _fake_get(url, headers=None, params=None, timeout=None):
        seen["get_url"] = url
        seen["get_headers"] = headers or {}
        return _FakeResponse(installations_payload)

    monkeypatch.setattr("app.auth.github_oauth.httpx.post", _fake_post)
    monkeypatch.setattr("app.auth.github_oauth.httpx.get", _fake_get)
    return seen


def test_exchange_returns_user_token_and_verified_identity(monkeypatch):
    seen = _wire(
        monkeypatch,
        token_payload={"access_token": "ghu_user", "refresh_token": "ghr_r", "expires_in": 28800},
        installations_payload={
            "installations": [
                {"id": 9999, "account": {"login": "other-org", "type": "Organization"}},
                {"id": 4242, "account": {"login": "acme-inc", "type": "Organization"}},
            ]
        },
    )

    tokens, installation_id = GitHubAppProvider(_settings()).exchange_code_with_installation(
        "code123", "4242"
    )

    assert tokens.access_token == "ghu_user"
    assert tokens.refresh_token == "ghr_r"
    # Identity comes from the VERIFIED installation record, never the query param.
    assert tokens.external_workspace_id == "acme-inc"
    assert installation_id == "4242"
    # GitHub returns form-encoded bodies unless JSON is explicitly requested.
    assert seen["post_headers"].get("Accept") == "application/json"
    assert seen["post_data"]["client_secret"] == "s3cret"
    assert seen["post_data"]["grant_type"] == "authorization_code"
    assert seen["get_url"].endswith("/user/installations")


def test_exchange_rejects_installation_id_not_owned_by_the_user(monkeypatch):
    """D4: the spoofing defence. See this module's docstring — do not weaken."""
    _wire(
        monkeypatch,
        token_payload={"access_token": "ghu_attacker"},
        installations_payload={
            # The attacker's token legitimately owns 1111 — but they claimed 4242,
            # which belongs to a victim organization.
            "installations": [{"id": 1111, "account": {"login": "attacker", "type": "User"}}]
        },
    )

    with pytest.raises(OAuthError) as excinfo:
        GitHubAppProvider(_settings()).exchange_code_with_installation("code123", "4242")

    assert "installation" in str(excinfo.value).lower()


def test_exchange_rejects_when_user_has_no_installations(monkeypatch):
    _wire(
        monkeypatch,
        token_payload={"access_token": "ghu_user"},
        installations_payload={"installations": []},
    )

    with pytest.raises(OAuthError):
        GitHubAppProvider(_settings()).exchange_code_with_installation("code123", "4242")


def test_exchange_raises_when_token_response_has_no_access_token(monkeypatch):
    _wire(
        monkeypatch,
        token_payload={"error": "bad_verification_code"},
        installations_payload={"installations": []},
    )

    with pytest.raises(OAuthError):
        GitHubAppProvider(_settings()).exchange_code_with_installation("code", "4242")


def test_plain_exchange_code_refuses_without_an_installation_id():
    """GitHub's flow genuinely needs a parameter the other providers don't.

    Failing loudly beats silently saving a connection with no installation id,
    which would only surface later as an unexplained ingest failure.
    """
    with pytest.raises(OAuthError) as excinfo:
        GitHubAppProvider(_settings()).exchange_code("code123")

    assert "installation_id" in str(excinfo.value)


def test_installation_id_is_compared_as_a_string(monkeypatch):
    """GitHub returns ``id`` as a JSON number; the redirect gives a string."""
    _wire(
        monkeypatch,
        token_payload={"access_token": "ghu_user"},
        installations_payload={
            "installations": [{"id": 4242, "account": {"login": "acme-inc"}}]
        },
    )

    tokens, _ = GitHubAppProvider(_settings()).exchange_code_with_installation("c", "4242")

    assert tokens.external_workspace_id == "acme-inc"


# -- factory ---------------------------------------------------------------


def test_factory_builds_the_github_provider(github_env):
    assert isinstance(build_oauth_provider("github"), GitHubAppProvider)


def test_factory_still_rejects_unknown_providers():
    with pytest.raises(ConfigurationError):
        build_oauth_provider("slack")


def test_resolve_installation_prefers_user_account_for_workspace_connect(monkeypatch):
    _wire(
        monkeypatch,
        token_payload={"access_token": "ghu_x"},
        installations_payload={
            "installations": [
                {
                    "id": 111,
                    "account": {"login": "acme-inc", "type": "Organization"},
                },
                {
                    "id": 222,
                    "account": {"login": "18-sana", "type": "User"},
                },
            ]
        },
    )
    tokens, iid = GitHubAppProvider(_settings()).exchange_code_resolve_installation(
        "code", prefer_user_account=True
    )
    assert iid == "222"
    assert tokens.external_workspace_id == "18-sana"


def test_resolve_installation_returns_none_when_app_not_installed(monkeypatch):
    _wire(
        monkeypatch,
        token_payload={"access_token": "ghu_x"},
        installations_payload={"installations": []},
    )
    assert (
        GitHubAppProvider(_settings()).exchange_code_resolve_installation("code")
        is None
    )
