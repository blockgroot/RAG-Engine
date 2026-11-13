"""GitHub ``OAuthProvider``.

1. Connect starts with user OAuth, which always returns to our callback; the
install screen is shown only when no installation is found. GitHub
enforces which repos a tenant granted.
2. The redirect's ``installation_id`` is untrusted (GitHub warns it can be
spoofed): ``_verify_installation`` checks it via ``/user/installations``
and the stored identity comes from that response, else an attacker could
bind a victim's org to their tenant.
3. The stored user token only proves who connected; repos are read with
short-lived installation tokens (``github_app.mint_installation_token``).
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

import httpx

from ..config.settings import GitHubSettings
from ..core.exceptions import ConfigurationError, OAuthError
from .base import OAuthProvider, OAuthTokens, compute_expires_at

_INSTALL_URL_TEMPLATE = "https://github.com/apps/{slug}/installations/new"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_INSTALLATIONS_URL = "https://api.github.com/user/installations"
_USER_URL = "https://api.github.com/user"
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
        """Start with user OAuth so reconnect works when the App is already installed.

        Sending people straight to the install page opens GitHub's settings for an
        existing install and never returns to our callback.
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
        """Not usable for GitHub: the installation id must be verified too.

        Raises so no caller stores a connection with no installation id.
        """
        raise OAuthError(
            "GitHub requires the callback's installation_id as well as the code — "
            "call exchange_code_with_installation(code, installation_id)."
        )

    def exchange_code_with_installation(
        self, code: str, installation_id: str
    ) -> tuple[OAuthTokens, str]:
        """Exchange the code and verify the claimed installation is the user's.

        Returns the tokens (workspace id = verified login) and the installation id.
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
        """Exchange ``code`` and list the App installations this user can see.

        The UI then asks which to bind; auto-picking put one personal install on
        both Company and a space.
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
        """Exchange ``code`` and pick one of the user's installations.

        For tests and offline callers; the HTTP callback uses the chooser instead.
        ``prefer_user_account=True`` (space connect) prefers a User install over an
        Organization one.
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
        """Refresh the user token (repo reads use installation tokens, never refreshed)."""
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

    def identify_user(self, code: str) -> tuple[str, str | None]:
        """``(login, display_name)`` of whoever authorized, for account linking.

        Reads ``/user`` once and discards the token: linking needs proof, not access.
        """
        return self._user(self._user_access_token(self._post_token_exchange(code)))

    def exchange_user_code(self, code: str) -> tuple[OAuthTokens, str, str | None]:
        """The person's own user-to-server token, kept, for Agent mode.

        Returns ``(tokens, login, display_name)``; it expires in eight hours with a
        single-use refresh token.
        """
        payload = self._post_token_exchange(code)
        token = self._user_access_token(payload)
        login, name = self._user(token)
        tokens = OAuthTokens(
            access_token=token,
            refresh_token=payload.get("refresh_token"),
            expires_at=compute_expires_at(payload.get("expires_in")),
            external_workspace_id=login,
            external_workspace_name=name,
        )
        return tokens, login, name

    @staticmethod
    def _user_access_token(payload: dict) -> str:
        token = payload.get("access_token")
        if not token:
            raise OAuthError(
                f"GitHub OAuth exchange returned no access token: {payload.get('error') or payload}"
            )
        return token

    def _user(self, token: str) -> tuple[str, str | None]:
        try:
            response = httpx.get(
                _USER_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": _API_VERSION,
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OAuthError(f"GitHub GET /user failed: {exc}", cause=exc) from exc
        user = response.json() or {}
        login = (user.get("login") or "").strip()
        if not login:
            raise OAuthError("GitHub did not return a login for this account")
        return login, (user.get("name") or "").strip() or None

    def _post_token_exchange(self, code: str) -> dict:
        """POST the code exchange.

        ``Accept: application/json`` is required: otherwise GitHub returns a form-encoded body.
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
            # A space connect means "my personal GitHub": falling back to an Organization
            # install exposed the company org, so return None and send them to install.
            return None

        # Org-wide connect: prefer an Organization installation but accept any,
        # since a small company may only ever have a User-account install.
        return installations[0]

    def _verify_installation(
        self, user_access_token: str, installation_id: str
    ) -> tuple[str, str | None]:
        """Confirm the user can access ``installation_id`` (the spoofing defence).

        Returns login/type from GitHub's response, so the stored identity is never attacker-supplied.
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

