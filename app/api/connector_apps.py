"""Admin routes for the company's own Google and Slack apps (``org_apps``).

Everything an admin must paste into Google or Slack is given here, filled in,
so the setup is copying values rather than knowing them.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth.org_apps import OWN_APP_PROVIDERS, app_key, get_org_app, save_org_app
from ..config.settings import ActionsSettings, ApiSettings, GoogleSettings, SlackSettings
from ..core.exceptions import ConfigurationError
from .deps import SessionClaims, require_admin

router = APIRouter(prefix="/admin/connector-apps", tags=["admin"])

# What Handbook's code expects of a Slack app. Kept equal to the self-hosted
# template (deploy/selfhost/apps/templates/slack-manifest.yml) by
# tests/test_own_connector_apps.py; change both together.
_SLACK_BOT_SCOPES = [
    "channels:history", "channels:read", "channels:join", "groups:history", "groups:read",
    "im:history", "im:read", "mpim:history", "mpim:read", "users:read", "users:read.email",
    "chat:write", "app_mentions:read", "files:write", "im:write",
]
_SLACK_USER_SCOPES = [
    "channels:read", "groups:read", "channels:history", "groups:history", "users:read",
    "users:read.email", "chat:write", "im:write",
]
_SLACK_BOT_EVENTS = ["app_mention", "message.im", "message.channels", "message.groups"]


def slack_manifest(api_base: str, key: str, redirect_urls: list[str]) -> dict:
    return {
        "display_information": {
            "name": "Handbook",
            "description": "Answers from your company's documents, with sources.",
        },
        "features": {
            "app_home": {
                "home_tab_enabled": False,
                "messages_tab_enabled": True,
                "messages_tab_read_only_enabled": False,
            },
            "bot_user": {"display_name": "Handbook", "always_online": True},
        },
        "oauth_config": {
            "redirect_urls": redirect_urls,
            "scopes": {"bot": _SLACK_BOT_SCOPES, "user": _SLACK_USER_SCOPES},
        },
        "settings": {
            "event_subscriptions": {
                "request_url": f"{api_base}/slack/events/{key}",
                "bot_events": _SLACK_BOT_EVENTS,
            },
            "interactivity": {
                "is_enabled": True,
                "request_url": f"{api_base}/slack/interactivity/{key}",
            },
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            # Handbook keeps the bot token, which never expires.
            "token_rotation_enabled": False,
        },
    }


def _view(org_id: str, provider: str) -> dict:
    agent_redirect = ActionsSettings.from_env().mcp_redirect_uri
    own = get_org_app(org_id, provider)
    if provider == "google":
        server = GoogleSettings.from_env()
        view = {"redirect_uris": [u for u in (server.redirect_uri, agent_redirect) if u]}
    else:
        server = SlackSettings.from_env()
        frontend = (ApiSettings.from_env().frontend_url or "").rstrip("/")
        if not frontend:
            raise HTTPException(status_code=409, detail="Set FRONTEND_URL so Slack can reach Handbook.")
        redirects = [u for u in (server.redirect_uri, agent_redirect) if u]
        manifest = slack_manifest(f"{frontend}/api", app_key(org_id, provider), redirects)
        text = json.dumps(manifest, indent=2)
        view = {
            "redirect_uris": redirects,
            "manifest": text,
            "create_url": "https://api.slack.com/apps?new_app=1&manifest_json="
            + quote(json.dumps(manifest)),
        }
    return {
        **view,
        "provider": provider,
        "configured": bool(own and own.configured),
        "client_id": own.client_id if own else None,
        # A self-hosted install's .env app; hosted Handbook has none.
        "server_app": bool(server.client_id and server.client_secret),
    }


@router.get("")
def list_connector_apps(session: SessionClaims = Depends(require_admin)):
    return {p: _view(session.org_id, p) for p in OWN_APP_PROVIDERS}


class SaveApp(BaseModel):
    client_id: str = Field(max_length=200)
    client_secret: str = Field(max_length=200)
    signing_secret: str | None = Field(default=None, max_length=200)


@router.put("/{provider}")
def save_connector_app(provider: str, body: SaveApp, session: SessionClaims = Depends(require_admin)):
    if provider not in OWN_APP_PROVIDERS:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        save_org_app(
            session.org_id, provider, client_id=body.client_id,
            client_secret=body.client_secret, signing_secret=body.signing_secret,
        )
    except ConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _view(session.org_id, provider)
