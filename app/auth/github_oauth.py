"""GitHub implementation of the ``OAuthProvider`` interface (Plan Phase 2).

Structurally this sits beside ``notion_oauth.py`` and ``google_oauth.py``, but
GitHub's flow differs from both in three ways that are worth understanding
before changing anything here:

1. **We send admins to an *install* page, not an authorize endpoint.**
   ``authorize_url`` returns ``https://github.com/apps/<slug>/installations/new``.
   Installing the App on a GitHub organization is what grants repository
   access, and the admin picks which repos on **GitHub's own screen** — so the
   tenant's repo scope is enforced by GitHub, not by a column in our database.
   That is the whole reason the App model was chosen over an OAuth App (plan
   decision D1) and it mirrors why Notion integration secrets are per-org
   (CLAUDE.md §2): the boundary is external and real.

2. **The ``installation_id`` from the redirect is untrusted input.** GitHub's
   documentation states plainly that "bad actors can hit this URL with a
   spoofed ``installation_id``", and recommends generating a user access token
   and confirming the installation belongs to that user. Skipping that would
   let an attacker bind a *victim's* GitHub organization to their own tenant
   and read its repositories through us. ``_verify_installation`` closes it via
   ``GET /user/installations``, and the identity we persist comes from that
   verified response — never from the query parameter. This is why the flow
   needs ``exchange_code_with_installation`` rather than the bare
   ``exchange_code`` the other two providers use.

3. **The token we store is not the token we read repos with.** The exchange
   yields a *user* access token, which proves who connected. Repository content
   is read with a short-lived *installation* token minted on demand from the
   App's private key (``github_app.mint_installation_token``, wired into
   ``credentials.get_live_connection_token``) — so nothing long-lived and
   repo-scoped is ever persisted (plan decision D2).

Zero new dependencies: plain ``httpx``, exactly as ``google_oauth.py`` does.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from ..config.settings import GitHubSettings
from ..core.exceptions import ConfigurationError, OAuthError
from .base import OAuthProvider, OAuthTokens

_INSTALL_URL_TEMPLATE = "https://github.com/apps/{slug}/installations/new"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_INSTALLATIONS_URL = "https://api.github.com/user/installations"
_API_VERSION = "2022-11-28"
_TIMEOUT = 15.0


class GitHubAppProvider(OAuthProvider):
    """Drives the GitHub App install + user-authorization flow for one org."""

    def __init__(self, settings: GitHubSettings | None = None) -> None:
        settings = settings or GitHubSettings.from_env()
        if not (settings.app_slug and settings.client_id and settings.client_secret):
            raise ConfigurationError(
                "GitHub OAuth requires GITHUB_APP_SLUG, GITHUB_CLIENT_ID, and "
                "GITHUB_CLIENT_SECRET to be set (register a GitHub App and enable "
                "'Request user authorization (OAuth) during installation')."
            )
        self._settings = settings

    # -- interface ---------------------------------------------------------

    def authorize_url(self, state: str) -> str:
        """Send the admin to GitHub's install screen, carrying our state."""
        base = _INSTALL_URL_TEMPLATE.format(slug=self._settings.app_slug)
        return f"{base}?{urlencode({'state': state})}"

    def exchange_code(self, code: str) -> OAuthTokens:
        """Not usable for GitHub — the installation id must be verified too.

        Raising here (rather than quietly succeeding) keeps a caller that
        treats GitHub like Notion/Google from persisting a connection with no
        installation id, which would otherwise only surface much later as an
        unexplained ingestion failure.
        """
        raise OAuthError(
            "GitHub requires the callback's installation_id as well as the code — "
            "call exchange_code_with_installation(code, installation_id)."
        )

    def exchange_code_with_installation(
        self, code: str, installation_id: str
    ) -> tuple[OAuthTokens, str]:
        """Exchange the code, then verify the claimed installation belongs to the user.

        Returns the tokens (with ``external_workspace_id`` set to the verified
        GitHub account login) and the verified installation id, which the
        caller persists to ``oauth_connections.source_config``.
        """
        if not installation_id:
            raise OAuthError("GitHub callback did not include an installation_id.")

        data = self._post_token_exchange(code)
        access_token = data.get("access_token")
        if not access_token:
            raise OAuthError(
                "GitHub OAuth response missing access_token "
                f"(error: {data.get('error', 'unknown')})."
            )

        account_login, account_type = self._verify_installation(access_token, installation_id)

        return (
            OAuthTokens(
                access_token=access_token,
                refresh_token=data.get("refresh_token"),
                expires_at=self._compute_expires_at(data.get("expires_in")),
                external_workspace_id=account_login,
                external_workspace_name=(
                    f"{account_login} ({account_type})" if account_type else account_login
                ),
            ),
            installation_id,
        )

    def refresh(self, refresh_token: str) -> OAuthTokens:
        """Refresh the *user* token.

        Note this is not on the critical path for reading repositories:
        installation tokens are minted from the App private key and never
        refreshed (plan decision D2). Implemented because GitHub Apps with
        expiring user tokens enabled do issue refresh tokens, and
        ``get_live_connection_token`` may want the user token to stay valid.
        """
        try:
            response = httpx.post(
                _TOKEN_URL,
                data={
                    "client_id": self._settings.client_id,
                    "client_secret": self._settings.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Accept": "application/json"},
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(f"GitHub token refresh failed: {exc}", cause=exc) from exc

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise OAuthError("GitHub refresh response missing access_token")
        return OAuthTokens(
            access_token=access_token,
            refresh_token=data.get("refresh_token"),
            expires_at=self._compute_expires_at(data.get("expires_in")),
            # The caller already knows the identity from the row being refreshed.
            external_workspace_id="",
            external_workspace_name=None,
        )

    # -- internals ---------------------------------------------------------

    def _post_token_exchange(self, code: str) -> dict:
        """POST the code exchange.

        ``Accept: application/json`` is load-bearing: without it GitHub returns
        a **form-encoded** body and ``response.json()`` blows up — a classic
        first-integration trap.
        """
        try:
            response = httpx.post(
                _TOKEN_URL,
                data={
                    "client_id": self._settings.client_id,
                    "client_secret": self._settings.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(f"GitHub OAuth code exchange failed: {exc}", cause=exc) from exc
        return response.json()

    def _verify_installation(
        self, user_access_token: str, installation_id: str
    ) -> tuple[str, str | None]:
        """Confirm ``installation_id`` is one this user actually has access to.

        See the module docstring (point 2) — this is the spoofing defence, not
        a nicety. Returns the account login/type taken from GitHub's response
        so the persisted identity can never be attacker-supplied.
        """
        try:
            response = httpx.get(
                _USER_INSTALLATIONS_URL,
                headers={
                    "Authorization": f"Bearer {user_access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": _API_VERSION,
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(
                f"GitHub installation verification failed: {exc}", cause=exc
            ) from exc

        installations = response.json().get("installations", []) or []
        for installation in installations:
            # GitHub returns ``id`` as a JSON number; the redirect supplies a
            # string. Compare as strings so the types can't silently mismatch.
            if str(installation.get("id")) == str(installation_id):
                account = installation.get("account") or {}
                login = account.get("login")
                if not login:
                    raise OAuthError(
                        "GitHub installation record is missing account.login."
                    )
                return login, account.get("type")

        raise OAuthError(
            f"The GitHub installation {installation_id!r} is not accessible to the "
            "account that authorized this connection. Reconnect from the "
            "organization you intend to link."
        )

    @staticmethod
    def _compute_expires_at(expires_in: object) -> datetime | None:
        if expires_in is None:
            return None
        try:
            seconds = int(expires_in)
        except (TypeError, ValueError):
            return None
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)
