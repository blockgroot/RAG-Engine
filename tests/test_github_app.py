"""GitHub App authentication primitives (GitHub Integration Plan, Phase 1).

Entirely offline: an RSA keypair is generated per-test and ``httpx`` is
monkeypatched at the module path, matching the established idiom in
``tests/test_auth.py`` / ``tests/test_google_oauth.py``. Nothing here needs a
DB, a real GitHub App, or the network.

The two behaviours under test are the ones GitHub constrains explicitly and
that would fail confusingly if we got them wrong:

1. The App JWT must be RS256 with a backdated ``iat`` and an ``exp`` no more
   than 10 minutes out — GitHub rejects the token otherwise.
2. The installation-token call must authenticate with that *App JWT*, never
   with an installation token (a subtle mix-up that yields a 401 loop).
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.github_app import build_app_jwt, mint_installation_token
from app.config.settings import GitHubSettings
from app.core.exceptions import ConfigurationError, OAuthError


def _pem() -> str:
    """A throwaway 2048-bit RSA private key in PEM form."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.fixture(scope="module")
def pem() -> str:
    # Key generation is the slow part; one key is enough for every case here.
    return _pem()


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


# -- build_app_jwt ---------------------------------------------------------


def test_app_jwt_is_rs256_with_backdated_iat_and_bounded_exp(pem: str) -> None:
    token = build_app_jwt(client_id="Iv1.abc123", private_key_pem=pem)

    assert jwt.get_unverified_header(token)["alg"] == "RS256"

    claims = jwt.decode(token, options={"verify_signature": False})
    now = int(time.time())
    assert claims["iss"] == "Iv1.abc123"
    # Backdated ~60s against clock drift (GitHub's own recommendation).
    assert claims["iat"] <= now - 55
    # GitHub hard-rejects anything more than 10 minutes out.
    assert claims["exp"] - claims["iat"] <= 600
    assert claims["exp"] > now


def test_app_jwt_verifies_against_the_matching_public_key(pem: str) -> None:
    """A real RS256 signature, not an unsigned/HS256 token."""
    token = build_app_jwt(client_id="Iv1.abc123", private_key_pem=pem)
    public_key = serialization.load_pem_private_key(pem.encode(), password=None).public_key()

    claims = jwt.decode(token, public_key, algorithms=["RS256"])

    assert claims["iss"] == "Iv1.abc123"


def test_app_jwt_requires_client_id_and_private_key(pem: str) -> None:
    with pytest.raises(ConfigurationError):
        build_app_jwt(client_id="", private_key_pem=pem)
    with pytest.raises(ConfigurationError):
        build_app_jwt(client_id="Iv1.abc123", private_key_pem="")


# -- mint_installation_token ----------------------------------------------


def test_mint_installation_token_authenticates_with_the_app_jwt(monkeypatch, pem: str) -> None:
    seen: dict = {}

    def _fake_post(url, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        return _FakeResponse({"token": "ghs_live_token", "expires_at": "2026-08-05T12:00:00Z"})

    monkeypatch.setattr("app.auth.github_app.httpx.post", _fake_post)
    settings = GitHubSettings(
        app_slug="acme-rag", client_id="Iv1.abc123", client_secret="s3cret", private_key=pem
    )

    result = mint_installation_token("12345678", settings)

    assert result.token == "ghs_live_token"
    assert result.expires_at is not None
    assert seen["url"].endswith("/app/installations/12345678/access_tokens")
    # The pinned API version is required by GitHub, not optional.
    assert seen["headers"]["X-GitHub-Api-Version"]
    # Crucially: this call is authenticated as the *App*, via the RS256 JWT.
    presented = seen["headers"]["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(presented, options={"verify_signature": False})
    assert claims["iss"] == "Iv1.abc123"


def test_mint_installation_token_raises_when_response_has_no_token(monkeypatch, pem: str) -> None:
    monkeypatch.setattr(
        "app.auth.github_app.httpx.post",
        lambda url, headers=None, timeout=None: _FakeResponse({"message": "Not Found"}),
    )
    settings = GitHubSettings(client_id="Iv1.abc123", private_key=pem)

    with pytest.raises(OAuthError):
        mint_installation_token("12345678", settings)


def test_mint_installation_token_wraps_transport_errors(monkeypatch, pem: str) -> None:
    import httpx

    def _boom(url, headers=None, timeout=None):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr("app.auth.github_app.httpx.post", _boom)
    settings = GitHubSettings(client_id="Iv1.abc123", private_key=pem)

    with pytest.raises(OAuthError) as excinfo:
        mint_installation_token("12345678", settings)
    # ProviderError convention: the original exception is always carried.
    assert excinfo.value.__cause__ is not None


# -- GitHubSettings --------------------------------------------------------


def test_settings_unescape_newlines_in_private_key(monkeypatch) -> None:
    """A PEM must survive .env files / secret managers that can't hold newlines."""
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "-----BEGIN X-----\\nline2\\n-----END X-----")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.abc123")

    settings = GitHubSettings.from_env()

    assert settings.private_key == "-----BEGIN X-----\nline2\n-----END X-----"
    assert settings.client_id == "Iv1.abc123"


def test_settings_default_to_none_when_unset(monkeypatch) -> None:
    for var in (
        "GITHUB_APP_SLUG",
        "GITHUB_CLIENT_ID",
        "GITHUB_CLIENT_SECRET",
        "GITHUB_APP_PRIVATE_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = GitHubSettings.from_env()

    assert settings.app_slug is None
    assert settings.private_key is None
