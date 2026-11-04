"""A company's own Google and Slack apps (``app/auth/org_apps.py``).

What must hold: sign-in uses the company's app; with no app anywhere a
connection says what to do instead of failing on every sync; saving an app
asks existing connections to reconnect; and a company's Slack app, which holds
its own signing secret, is trusted only for that company's Slack workspace.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import OAuthTokens, save_connection
from app.auth.factory import build_oauth_provider
from app.auth.org_apps import app_key, google_settings, save_org_app
from app.core.exceptions import ConfigurationError, OAuthReauthRequiredError
from app.db.connection import get_connection

from .conftest import requires_db

GOOGLE_ID = "123456789012-abcdef123456.apps.googleusercontent.com"
SLACK_ID = "1234567890.9876543210"
SIGNING = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _hosted(monkeypatch):
    """Hosted Handbook: no shared Google or Slack app in settings."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("FRONTEND_URL", "https://hb.example")
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SLACK_CLIENT_ID",
                 "SLACK_CLIENT_SECRET", "SLACK_SIGNING_SECRET"):
        monkeypatch.delenv(name, raising=False)


def test_a_key_in_the_wrong_box_is_refused_before_saving():
    with pytest.raises(ConfigurationError, match="isn't a Google Client ID"):
        save_org_app("org", "google", client_id="1234.5678", client_secret="s")
    with pytest.raises(ConfigurationError, match="isn't a Slack Signing Secret"):
        save_org_app("org", "slack", client_id=SLACK_ID, client_secret="s", signing_secret="xoxb-1")


def test_the_slack_template_matches_the_self_hosted_one():
    yaml = pytest.importorskip("yaml")
    from app.api.connector_apps import slack_manifest

    path = Path(__file__).parents[1] / "deploy/selfhost/apps/templates/slack-manifest.yml"
    text = path.read_text().replace("HANDBOOK_DOMAIN", "hb.example")
    hosted = slack_manifest("https://hb.example/api", "KEY", [
        "https://hb.example/api/auth/slack/callback", "https://hb.example/api/act/mcp/callback"])
    settings = hosted["settings"]
    settings["event_subscriptions"]["request_url"] = "https://hb.example/api/slack/events"
    settings["interactivity"]["request_url"] = "https://hb.example/api/slack/interactivity"
    assert hosted == yaml.safe_load(text)


@requires_db
def test_sign_in_uses_the_companys_own_app_and_never_another_companys(store, org_cleanup):
    acme = store.create_organization("Own App Acme")
    other = store.create_organization("Own App Other")
    org_cleanup.extend([acme, other])

    with pytest.raises(ConfigurationError, match="Google app isn't set up"):
        build_oauth_provider("google", org_id=acme)

    save_org_app(acme, "google", client_id=GOOGLE_ID, client_secret="gsecret")
    assert f"client_id={GOOGLE_ID}" in build_oauth_provider("google", org_id=acme).authorize_url("s")
    assert google_settings(acme).client_secret == "gsecret"
    with pytest.raises(ConfigurationError):
        build_oauth_provider("google", org_id=other)


@requires_db
def test_with_no_app_a_connection_asks_for_one_and_saving_an_app_asks_to_reconnect(store, org_cleanup):
    from app.auth.credentials import get_live_connection_token

    org = store.create_organization("Own App Reauth")
    org_cleanup.append(org)
    save_connection(org, "slack", OAuthTokens(
        access_token="xoxb-old", refresh_token=None, expires_at=None,
        external_workspace_id="T-OWN-1", external_workspace_name="Acme"))

    with pytest.raises(OAuthReauthRequiredError, match="own Slack app"):
        get_live_connection_token(org, "slack")

    save_org_app(org, "slack", client_id=SLACK_ID, client_secret="ssecret", signing_secret=SIGNING)
    with get_connection() as conn:
        flagged, reason = conn.execute(
            "SELECT needs_reauth, reauth_reason FROM oauth_connections WHERE org_id = %s",
            (org,)).fetchone()
    assert flagged and "app was changed" in reason


def _signed(body: dict, secret: str) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + raw,
                           hashlib.sha256).hexdigest()
    return raw, {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
                 "Content-Type": "application/json"}


@requires_db
def test_a_companys_slack_app_is_trusted_only_for_its_own_workspace(store, org_cleanup, monkeypatch):
    from app.api import slack_events
    from app.api.main import create_app

    acme = store.create_organization("Own Slack Acme")
    other = store.create_organization("Own Slack Other")
    org_cleanup.extend([acme, other])
    for org, team in ((acme, "T-OWN-ACME"), (other, "T-OWN-OTHER")):
        save_connection(org, "slack", OAuthTokens(
            access_token="xoxb", refresh_token=None, expires_at=None,
            external_workspace_id=team, external_workspace_name=team))
    save_org_app(acme, "slack", client_id=SLACK_ID, client_secret="s", signing_secret=SIGNING)
    with get_connection() as conn:  # the app was just saved; these are reconnected
        conn.execute("UPDATE oauth_connections SET needs_reauth = false WHERE org_id = %s", (acme,))
    key = app_key(acme, "slack")
    handled = []
    monkeypatch.setattr(slack_events, "_handle", lambda event, team: handled.append(team))
    client = TestClient(create_app())

    assert client.post("/slack/events/no-such-key", json={}).status_code == 404
    raw, headers = _signed({"type": "url_verification", "challenge": "c1"}, "f" * 32)
    assert client.post(f"/slack/events/{key}", content=raw, headers=headers).status_code == 401
    raw, headers = _signed({"type": "url_verification", "challenge": "c1"}, SIGNING)
    assert client.post(f"/slack/events/{key}", content=raw, headers=headers).json() == {"challenge": "c1"}

    for team in ("T-OWN-OTHER", "T-OWN-ACME"):
        raw, headers = _signed({"type": "event_callback", "team_id": team, "event": {
            "type": "app_mention", "channel": "C1", "user": "U1", "text": "hi"}}, SIGNING)
        assert client.post(f"/slack/events/{key}", content=raw, headers=headers).status_code == 200
    assert handled == ["T-OWN-ACME"]
