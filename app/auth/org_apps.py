"""A company's own Google or Slack app (``org_connector_apps``).

Our shared Google app is in Testing, so only listed test users can sign in and
their tokens die in 7 days; publishing it means a yearly paid audit for
``drive.readonly``. Our shared Slack app, installed in other workspaces without
Marketplace approval, reads history at about one request a minute. A company's
own app has neither problem: Google treats it as Internal, Slack as internal.
Onyx and Glean ask for the same thing.

The keys from ``.env`` stay the fallback: a self-hosted install's ``.env`` IS
the company's own app. Hosted Handbook leaves those keys unset, which is what
makes the company's own app required there.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, replace

from ..config.settings import GoogleSettings, SlackSettings
from ..core.exceptions import ConfigurationError
from ..db.connection import get_connection
from ..security import decrypt, encrypt

OWN_APP_PROVIDERS = ("google", "slack")

_GOOGLE_CLIENT_ID = re.compile(r"^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$")
_SLACK_CLIENT_ID = re.compile(r"^[0-9]+\.[0-9]+$")
_SLACK_SIGNING_SECRET = re.compile(r"^[0-9a-f]{32}$")

_NAMES = {"google": "Google", "slack": "Slack"}


@dataclass(frozen=True)
class OrgApp:
    """Non-secret view of a company's app, safe to show an admin."""

    provider: str
    app_key: str
    client_id: str | None

    @property
    def configured(self) -> bool:
        return bool(self.client_id)


def _check(provider: str) -> None:
    if provider not in OWN_APP_PROVIDERS:
        raise ConfigurationError(f"{provider!r} has no company app setting.")


def app_key(org_id: str, provider: str) -> str:
    """This company's key for ``provider``, created on first use.

    Created before the keys are saved because it is written into the Slack app
    template the admin pastes before Slack shows them any keys."""
    _check(provider)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO org_connector_apps (org_id, provider, app_key) VALUES (%s, %s, %s) "
            "ON CONFLICT (org_id, provider) DO NOTHING",
            (org_id, provider, secrets.token_urlsafe(24)),
        )
        row = conn.execute(
            "SELECT app_key FROM org_connector_apps WHERE org_id = %s AND provider = %s",
            (org_id, provider),
        ).fetchone()
    return row[0]


def get_org_app(org_id: str, provider: str) -> OrgApp | None:
    _check(provider)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT app_key, client_id FROM org_connector_apps WHERE org_id = %s AND provider = %s",
            (org_id, provider),
        ).fetchone()
    return OrgApp(provider, row[0], row[1]) if row else None


def _validate(provider: str, client_id: str, client_secret: str, signing_secret: str | None) -> None:
    """Catch a key pasted into the wrong box before anyone tries to sign in."""
    if not client_secret:
        raise ConfigurationError("Paste the Client Secret.")
    if provider == "google":
        if not _GOOGLE_CLIENT_ID.match(client_id):
            raise ConfigurationError(
                "That isn't a Google Client ID. It ends in .apps.googleusercontent.com."
            )
        return
    if not _SLACK_CLIENT_ID.match(client_id):
        raise ConfigurationError(
            "That isn't a Slack Client ID. It is two numbers with a dot between them."
        )
    if not signing_secret or not _SLACK_SIGNING_SECRET.match(signing_secret):
        raise ConfigurationError(
            "That isn't a Slack Signing Secret. It is 32 letters and numbers, "
            "under Basic Information → App Credentials."
        )


def save_org_app(
    org_id: str,
    provider: str,
    *,
    client_id: str,
    client_secret: str,
    signing_secret: str | None = None,
) -> OrgApp:
    """Save the company's app. Existing connections must reconnect.

    A token belongs to the app that issued it: Google refuses to refresh it with
    any other app's keys, and a Slack bot token from another app answers to
    that app's events. So every connection of this provider in the company,
    spaces included, is asked to reconnect, rather than failing later."""
    _check(provider)
    client_id, client_secret = client_id.strip(), client_secret.strip()
    signing_secret = (signing_secret or "").strip() or None
    _validate(provider, client_id, client_secret, signing_secret)
    key = app_key(org_id, provider)
    with get_connection() as conn:
        conn.execute(
            "UPDATE org_connector_apps SET client_id = %s, client_secret_encrypted = %s, "
            "signing_secret_encrypted = %s, updated_at = now() "
            "WHERE org_id = %s AND provider = %s",
            (
                client_id,
                encrypt(client_secret),
                encrypt(signing_secret) if signing_secret else None,
                org_id,
                provider,
            ),
        )
        conn.execute(
            "UPDATE oauth_connections SET needs_reauth = true, reauth_reason = %s "
            "WHERE org_id = %s AND provider = %s",
            (
                f"Your company's {_NAMES[provider]} app was changed. Reconnect so it is used.",
                org_id,
                provider,
            ),
        )
    return OrgApp(provider, key, client_id)


def _secrets(org_id: str, provider: str) -> tuple[str, str, str | None] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT client_id, client_secret_encrypted, signing_secret_encrypted "
            "FROM org_connector_apps WHERE org_id = %s AND provider = %s AND client_id IS NOT NULL",
            (org_id, provider),
        ).fetchone()
    if not row:
        return None
    return row[0], decrypt(row[1]), decrypt(row[2]) if row[2] else None


def google_settings(org_id: str | None) -> GoogleSettings:
    """``GoogleSettings`` with this company's app, else ``.env``'s."""
    settings = GoogleSettings.from_env()
    own = _secrets(org_id, "google") if org_id else None
    if own:
        return replace(settings, client_id=own[0], client_secret=own[1])
    return settings


def slack_settings(org_id: str | None) -> SlackSettings:
    """``SlackSettings`` with this company's app, else ``.env``'s."""
    settings = SlackSettings.from_env()
    own = _secrets(org_id, "slack") if org_id else None
    if own:
        return replace(settings, client_id=own[0], client_secret=own[1], signing_secret=own[2])
    return settings


def has_app(org_id: str, provider: str) -> bool:
    """Whether this company can sign in to ``provider`` at all."""
    settings = google_settings(org_id) if provider == "google" else slack_settings(org_id)
    return bool(settings.client_id and settings.client_secret)


def missing_app_reason(org_id: str, provider: str) -> str | None:
    """Why a connection of ``provider`` cannot work: its company has no app.

    Such a row is only flagged when its token is next used, and a Slack bot
    token is used only by a sync, so a list showed it as healthy for weeks."""
    if provider not in OWN_APP_PROVIDERS or has_app(org_id, provider):
        return None
    return f"Set up your company's own {_NAMES[provider]} app under Sources, then reconnect."


def org_for_app_key(provider: str, key: str) -> tuple[str, str | None] | None:
    """``(org_id, signing_secret)`` for a Slack request's app key."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id, signing_secret_encrypted FROM org_connector_apps "
            "WHERE provider = %s AND app_key = %s AND client_id IS NOT NULL",
            (provider, key),
        ).fetchone()
    if not row:
        return None
    return str(row[0]), decrypt(row[1]) if row[1] else None
