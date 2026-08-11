"""Email dispatch: console / smtp / resend. No live network."""

from __future__ import annotations

import pytest

from app.auth.email import _dispatch
from app.config.settings import EmailSettings
from app.core.exceptions import ConfigurationError, ProviderError


def test_resend_posts_to_https_api(monkeypatch):
    captured: dict = {}

    class _Response:
        status_code = 200
        text = '{"id":"re_test"}'

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _Response()

    monkeypatch.setattr("app.auth.email.httpx.Client", _Client)

    settings = EmailSettings(
        sender="resend",
        smtp_from="Handbook <beth.t@example.com>",
        resend_api_key="re_test_key",
    )
    _dispatch("owner@example.com", "Subject", "plain body", settings, html_body="<p>hi</p>")

    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_test_key"
    assert captured["json"]["to"] == ["owner@example.com"]
    assert captured["json"]["html"] == "<p>hi</p>"
    assert captured["json"]["text"] == "plain body"


def test_resend_requires_api_key():
    settings = EmailSettings(sender="resend", smtp_from="noreply@example.com")
    with pytest.raises(ConfigurationError, match="EMAIL_RESEND_API_KEY"):
        _dispatch("a@b.com", "s", "b", settings)


def test_smtp_unreachable_names_render_free_block(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError(101, "Network is unreachable")

    monkeypatch.setattr("app.auth.email.smtplib.SMTP", _boom)
    settings = EmailSettings(
        sender="smtp",
        smtp_host="smtp.gmail.com",
        smtp_from="a@b.com",
    )
    with pytest.raises(ProviderError, match="blocks outbound SMTP"):
        _dispatch("a@b.com", "s", "b", settings)


def test_unknown_sender_rejected():
    with pytest.raises(ConfigurationError, match="resend"):
        _dispatch("a@b.com", "s", "b", EmailSettings(sender="pigeon"))


def test_resend_surfaces_provider_error_body(monkeypatch):
    class _Response:
        status_code = 403
        text = "domain not verified"

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            return _Response()

    monkeypatch.setattr("app.auth.email.httpx.Client", _Client)
    settings = EmailSettings(
        sender="resend",
        smtp_from="me@example.com",
        resend_api_key="re_x",
    )
    with pytest.raises(ProviderError, match="domain not verified"):
        _dispatch("a@b.com", "s", "b", settings)
