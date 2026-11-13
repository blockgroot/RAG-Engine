"""GitHub App authentication: the App JWT and installation tokens.

``build_app_jwt`` enforces GitHub's limits (``exp`` within 10 minutes,
``iat`` backdated 60s), since a mistake there is an opaque 401.
``mint_installation_token`` trades it for a 1-hour installation token.
Nothing is stored: a token can be re-minted any time from the key and
``installation_id``.
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

    ``X-GitHub-Api-Version`` is pinned so the API cannot change under us;
    ``accept`` is the caller's (raw contents, diffs).
    """
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def build_app_jwt(*, client_id: str | None, private_key_pem: str | None) -> str:
    """Sign a short-lived RS256 JWT as the GitHub App (``iss`` = client id).

    Raises ``ConfigurationError`` when a credential is missing, instead of a 401.
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


#: GitHub permissions that have no ``read`` level ("Can be one of: write").
#: Reading never needs them, so a read-only token simply leaves them out.
_NO_READ_LEVEL = frozenset({"workflows"})


def installation_permissions(
    installation_id: str, settings: GitHubSettings | None = None
) -> dict[str, str]:
    """What the installation's owner granted the App, e.g. ``{"issues": "write"}``.

    Never cached: Act tells "not approved yet" from "approved" with it.
    """
    permissions = _installation(installation_id, settings).get("permissions") or {}
    return {str(k): str(v) for k, v in permissions.items()}


def installation_settings_url(
    installation_id: str, settings: GitHubSettings | None = None
) -> str | None:
    """GitHub's settings page for this installation, as GitHub gives it.

    A hand-built ``/settings/installations/<id>`` is a 404 for an organization.
    """
    url = str(_installation(installation_id, settings).get("html_url") or "")
    return url if url.startswith("https://github.com/") else None


def _installation(installation_id: str, settings: GitHubSettings | None = None) -> dict:
    """``GET /app/installations/<id>``, as the App."""
    settings = settings or GitHubSettings.from_env()
    app_jwt = build_app_jwt(client_id=settings.client_id, private_key_pem=settings.private_key)
    try:
        response = httpx.get(
            f"{GITHUB_API_BASE}/app/installations/{installation_id}",
            headers=github_headers(app_jwt),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OAuthError(
            f"GitHub installation lookup failed for installation {installation_id}: {exc}",
            cause=exc,
        ) from exc
    return response.json() or {}


def read_only(permissions: dict[str, str]) -> dict[str, str]:
    """Every granted permission at ``read``, and nothing that cannot be read."""
    return {
        name: "read"
        for name, level in permissions.items()
        if level in ("read", "write", "admin") and name not in _NO_READ_LEVEL
    }


def mint_installation_token(
    installation_id: str, settings: GitHubSettings | None = None
) -> InstallationToken:
    """Exchange an App JWT for a 1-hour, READ-ONLY installation access token.

    Repositories are not narrowed (the installation is the tenant's scope).
    Permissions are narrowed to ``read`` on every grant: the App also holds
    writes for Act, which writes only with a person's token. Fails closed: no
    readable grant, no token.
    """
    settings = settings or GitHubSettings.from_env()
    permissions = read_only(installation_permissions(installation_id, settings))
    if not permissions:
        raise OAuthError(f"GitHub installation {installation_id} grants no readable permissions")
    app_jwt = build_app_jwt(
        client_id=settings.client_id, private_key_pem=settings.private_key
    )
    try:
        response = httpx.post(
            f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens",
            headers=github_headers(app_jwt),
            json={"permissions": permissions},
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
