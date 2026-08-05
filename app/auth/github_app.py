"""GitHub App authentication primitives (GitHub Integration Plan, Phase 1).

Two responsibilities live here, both independent of the database and of any
``org_id`` — the credentials layer (``credentials.py``) owns that context and
calls into this module:

1. ``build_app_jwt`` signs the short-lived RS256 JWT that authenticates us as
   the **App itself** (not as an installation, and not as a user). GitHub
   constrains this token tightly: ``exp`` must be no more than 10 minutes out,
   and it recommends backdating ``iat`` by 60 seconds against clock drift.
   Both are enforced here rather than left to callers, because getting either
   wrong produces an opaque 401 rather than a useful error.
2. ``mint_installation_token`` exchanges that App JWT for a 1-hour
   **installation access token**, which is the credential that actually reads
   repo content.

**Why nothing is persisted here** (plan decision D2): an installation token
lasts an hour and can be re-minted at any moment from the private key plus the
installation id, so storing one would add a refresh lifecycle that buys
nothing. What *is* stored on the ``oauth_connections`` row is the user access
token (proof of who connected) and the ``installation_id`` in
``source_config``.

Zero new dependencies: ``pyjwt`` already signs session tokens in
``app/auth/session.py`` and ``cryptography`` already backs
``app/security/crypto.py`` — RS256 needs exactly those two (plan decision D7,
the same dependency-light reasoning as ``httpx``-over-``google-api-python-client``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import httpx
import jwt

from ..config.settings import GitHubSettings
from ..core.exceptions import ConfigurationError, OAuthError

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"

# 9 minutes — comfortably inside GitHub's 10-minute ceiling even after the
# 60-second backdating below (which counts against the same window).
_JWT_TTL_SECONDS = 540
_IAT_BACKDATE_SECONDS = 60
_TIMEOUT = 15.0


def github_headers(
    token: str, *, accept: str = "application/vnd.github+json"
) -> dict[str, str]:
    """Standard GitHub REST headers.

    ``X-GitHub-Api-Version`` is pinned deliberately: GitHub's REST API is
    versioned by date and an unpinned client silently inherits whatever the
    current default is. ``accept`` is a parameter because several endpoints we
    need return meaningfully different payloads per media type (raw file
    contents, commit diffs), and that choice belongs to the caller.
    """
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def build_app_jwt(*, client_id: str | None, private_key_pem: str | None) -> str:
    """Sign a short-lived RS256 JWT authenticating as the GitHub App.

    ``iss`` is the App's client id (GitHub accepts either the client id or the
    numeric app id; the client id is what the rest of this integration already
    configures). Raises ``ConfigurationError`` when either credential is
    missing, so a misconfigured deployment fails with an actionable message
    instead of a 401 from GitHub.
    """
    if not client_id or not private_key_pem:
        raise ConfigurationError(
            "GitHub App JWT requires GITHUB_CLIENT_ID and GITHUB_APP_PRIVATE_KEY "
            "(create a GitHub App and generate a private key to obtain these)."
        )
    now = int(time.time())
    try:
        return jwt.encode(
            {
                "iat": now - _IAT_BACKDATE_SECONDS,
                "exp": now - _IAT_BACKDATE_SECONDS + _JWT_TTL_SECONDS,
                "iss": client_id,
            },
            private_key_pem,
            algorithm="RS256",
        )
    except Exception as exc:  # malformed PEM, wrong key type, …
        raise ConfigurationError(
            f"GitHub App private key could not be used to sign a JWT: {exc}", cause=exc
        ) from exc


@dataclass(frozen=True)
class InstallationToken:
    """A minted installation access token and when it stops working."""

    token: str
    expires_at: datetime | None


def mint_installation_token(
    installation_id: str, settings: GitHubSettings | None = None
) -> InstallationToken:
    """Exchange an App JWT for a 1-hour installation access token.

    The returned token carries whatever repositories and permissions the admin
    granted on GitHub's install screen — we deliberately do not narrow it with
    the ``repositories``/``permissions`` body parameters, because the
    installation *is* the tenant's declared scope and re-declaring it here
    would let our copy drift from GitHub's authoritative one.
    """
    settings = settings or GitHubSettings.from_env()
    app_jwt = build_app_jwt(
        client_id=settings.client_id, private_key_pem=settings.private_key
    )
    try:
        response = httpx.post(
            f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens",
            headers=github_headers(app_jwt),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OAuthError(
            f"GitHub installation-token request failed for installation "
            f"{installation_id}: {exc}",
            cause=exc,
        ) from exc

    data = response.json()
    token = data.get("token")
    if not token:
        raise OAuthError(
            "GitHub installation-token response missing 'token' — the App may "
            "have been uninstalled, or its private key rotated."
        )
    return InstallationToken(token=token, expires_at=_parse_expiry(data.get("expires_at")))


def _parse_expiry(value: str | None) -> datetime | None:
    """Parse GitHub's RFC3339 ``expires_at`` (same ``Z``-suffix trick as elsewhere)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
