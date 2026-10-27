"""Each tool's sign-in return address defaults from FRONTEND_URL.

The self-hosted install sets only FRONTEND_URL; a missing address would leave
that tool unconfigured. An address set explicitly (Cloud, local dev) wins.
"""

from __future__ import annotations

import pytest

from app.config.settings import (
    GoogleSettings,
    LinearSettings,
    NotionSettings,
    SlackSettings,
)

TOOLS = [
    (NotionSettings, "NOTION_REDIRECT_URI", "notion"),
    (LinearSettings, "LINEAR_REDIRECT_URI", "linear"),
    (GoogleSettings, "GOOGLE_REDIRECT_URI", "google"),
    (SlackSettings, "SLACK_REDIRECT_URI", "slack"),
]


@pytest.mark.parametrize("settings, name, provider", TOOLS)
def test_the_address_comes_from_the_frontend_origin(monkeypatch, settings, name, provider):
    monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FRONTEND_URL", "https://handbook.acme.com/")
    assert settings.from_env().redirect_uri == (
        f"https://handbook.acme.com/api/auth/{provider}/callback"
    )


@pytest.mark.parametrize("settings, name, provider", TOOLS)
def test_an_explicit_address_wins(monkeypatch, settings, name, provider):
    monkeypatch.setenv(name, "http://localhost:8000/custom")
    monkeypatch.setenv("FRONTEND_URL", "https://handbook.acme.com")
    assert settings.from_env().redirect_uri == "http://localhost:8000/custom"


@pytest.mark.parametrize("settings, name, provider", TOOLS)
def test_no_address_without_a_frontend(monkeypatch, settings, name, provider):
    monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    assert settings.from_env().redirect_uri is None
