"""GitHub implementation of the ``OAuthProvider`` interface (Plan Phase 2).

Structurally this sits beside ``notion_oauth.py`` and ``google_oauth.py``, but
GitHub's flow differs from both in three ways that are worth understanding
before changing anything here:

1. **Connect starts with user OAuth; install is only if needed.**
   ``authorize_url`` returns ``/login/oauth/authorize``. That always returns
   to our callback even when BrowseSource is *already* installed (common when
   org Sources connected first, then a workspace). If OAuth finds no
   installation, the callback redirects to ``install_url``
   (``/apps/<slug>/installations/new``). Repo access is still granted on
   GitHub's install screen — the tenant's repo scope is enforced by GitHub,
   not by a column in our database (plan decision D1).

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
from .base import OAuthProvider, OAuthTokens, compute_expires_at

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
        """Start with *user* OAuth so reconnect works when the App is already installed.

        The old flow sent people straight to ``/apps/<slug>/installations/new``.
        That is correct for a first install, but when BrowseSource is already
        installed on the account (common: org Sources connected earlier, then a
        workspace tries to connect), GitHub opens the *settings* page for the
        existing installation instead of completing our callback — Handbook never
        gets ``code``/``state``, so the workspace row is never created. Org
        "Refresh list" still works because that row already existed.

        User OAuth always returns to our callback. If the App is not installed
        yet, the callback redirects to the install screen (with a fresh state).
        """
        return (
            "https://github.com/login/oauth/authorize?"
            + urlencode(
                {
                    "client_id": self._settings.client_id,
                    "state": state,
                }
            )
        )

    def install_url(self, state: str) -> str:
        """GitHub App install screen — used when OAuth finds no installation yet."""
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
                expires_at=compute_expires_at(data.get("expires_in")),
                external_workspace_id=account_login,
                external_workspace_name=(
                    f"{account_login} ({account_type})" if account_type else account_login
                ),
            ),
            installation_id,
        )

    def exchange_code_list_installations(
        self, code: str
    ) -> tuple[str, str | None, datetime | None, list[dict]]:
        """Exchange ``code`` and list App installations this user can see.

        Used by the connect callback when GitHub did not already hand an
        ``installation_id``: we park the user token and let the UI prompt
        which account to bind (Company Sources vs a space) instead of
        silently auto-picking — that auto-pick is how the same personal
        install ended up on both surfaces.
        """
        data = self._post_token_exchange(code)
        access_token = data.get("access_token")
        if not access_token:
            raise OAuthError(
                "GitHub OAuth response missing access_token "
                f"(error: {data.get('error', 'unknown')})."
            )
        installations = self._list_installations(access_token)
        return (
            access_token,
            data.get("refresh_token"),
            compute_expires_at(data.get("expires_in")),
            installations,
        )

    def tokens_for_installation(
        self,
        access_token: str,
        installation_id: str,
        *,
        refresh_token: str | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[OAuthTokens, str]:
        """Verify ``installation_id`` against the user token and build tokens."""
        account_login, account_type = self._verify_installation(
            access_token, installation_id
        )
        return (
            OAuthTokens(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
                external_workspace_id=account_login,
                external_workspace_name=(
                    f"{account_login} ({account_type})" if account_type else account_login
                ),
            ),
            str(installation_id),
        )

    def exchange_code_resolve_installation(
        self,
        code: str,
        *,
        prefer_user_account: bool = False,
    ) -> tuple[OAuthTokens, str] | None:
        """Exchange ``code``, then pick an installation this user already has.

        Kept for unit tests and offline callers. The HTTP connect callback
        no longer auto-picks — it uses ``exchange_code_list_installations``
        and the install-chooser UI instead.

        ``prefer_user_account=True`` (workspace connect) prefers a User
        installation over an Organization one, so a personal space does not
        silently bind the company GitHub org install when both exist.
        """
        access_token, refresh_token, expires_at, installations = (
            self.exchange_code_list_installations(code)
        )
        if not installations:
            return None

        chosen = self._pick_installation(
            installations, prefer_user_account=prefer_user_account
        )
        if chosen is None:
            return None

        return self.tokens_for_installation(
            access_token,
            str(chosen.get("id")),
            refresh_token=refresh_token,
            expires_at=expires_at,
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
            expires_at=compute_expires_at(data.get("expires_in")),
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

    def _list_installations(self, user_access_token: str) -> list[dict]:
        """Installations visible to this user access token."""
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
        return list(response.json().get("installations", []) or [])

    @staticmethod
    def _pick_installation(
        installations: list[dict], *, prefer_user_account: bool
    ) -> dict | None:
        if not installations:
            return None
        preferred_type = "User" if prefer_user_account else "Organization"
        for installation in installations:
            account = installation.get("account") or {}
            if account.get("type") == preferred_type:
                return installation

        if prefer_user_account:
            # A workspace connect is explicitly "connect MY personal GitHub".
            # Falling back to an Organization installation here is how a personal
            # space silently became a window onto the whole company org — so
            # return None instead and let the caller send them to GitHub's
            # install screen, where they can install on their own account.
            return None

        # Org-wide connect: prefer an Organization installation but accept any,
        # since a small company may only ever have a User-account install.
        return installations[0]

    def _verify_installation(
        self, user_access_token: str, installation_id: str
    ) -> tuple[str, str | None]:
        """Confirm ``installation_id`` is one this user actually has access to.

        See the module docstring (point 2) — this is the spoofing defence, not
        a nicety. Returns the account login/type taken from GitHub's response
        so the persisted identity can never be attacker-supplied.
        """
        installations = self._list_installations(user_access_token)
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

