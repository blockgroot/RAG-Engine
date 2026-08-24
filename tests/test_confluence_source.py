"""ConfluenceAdapter tests: pagination, HTML->text conversion, per-org
credential resolution. No network — httpx.get is monkeypatched to return
canned REST v2 responses.
"""

from __future__ import annotations

import pytest

from app.config.settings import ConfluenceSettings
from app.core.exceptions import ConfigurationError, SourceError
from app.sources import build_source_adapter
from app.sources.confluence import ConfluenceAdapter, _html_to_text


class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise Exception("HTTP error")

    def json(self):
        return self._payload


def test_resolve_never_falls_back(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://default.atlassian.net/wiki")
    monkeypatch.setenv("CONFLUENCE_EMAIL", "default@x.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "default-token")
    monkeypatch.setenv("CONFLUENCE_BASE_URL_ACME", "https://acme.atlassian.net/wiki")
    monkeypatch.setenv("CONFLUENCE_EMAIL_ACME", "you@acme.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN_ACME", "tok-acme")
    settings = ConfluenceSettings.from_env()
    credential = settings.resolve("acme")
    assert credential.base_url == "https://acme.atlassian.net/wiki"
    assert credential.token == "tok-acme"
    with pytest.raises(ConfigurationError):
        settings.resolve("globex")


def test_missing_credential_raises_configuration_error(monkeypatch):
    for key in ("CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ConfigurationError):
        ConfluenceAdapter(settings=ConfluenceSettings.from_env())


def test_html_to_text_strips_tags_and_keeps_line_breaks():
    html = "<p>Hello <strong>world</strong></p><ul><li>one</li><li>two</li></ul>"
    text = _html_to_text(html)
    assert "Hello world" in text
    assert "one" in text and "two" in text


def test_list_documents_paginates(monkeypatch):
    pages = [
        {
            "results": [{"id": "1", "title": "A", "version": {"createdAt": "2026-01-01T00:00:00Z"}}],
            "_links": {"next": "/api/v2/pages?cursor=c1"},
        },
        {
            "results": [{"id": "2", "title": "B", "version": {"createdAt": "2026-01-02T00:00:00Z"}}],
            "_links": {},
        },
    ]

    def fake_get(url, params, auth, timeout):
        assert auth == ("you@acme.com", "tok-acme")
        return _FakeResponse(pages.pop(0))

    monkeypatch.setattr("app.sources.confluence.httpx.get", fake_get)
    from app.config.settings import ConfluenceCredential

    adapter = ConfluenceAdapter(
        credential=ConfluenceCredential("https://acme.atlassian.net/wiki", "you@acme.com", "tok-acme")
    )
    refs = adapter.list_documents()
    assert [r.external_id for r in refs] == ["1", "2"]


def test_fetch_document_converts_html_body(monkeypatch):
    payload = {
        "id": "1",
        "title": "Health Allowance",
        "version": {"createdAt": "2026-01-01T00:00:00Z"},
        "body": {"export_view": {"value": "<p>Permissible expenses include gym.</p>"}},
        "_links": {"webui": "/spaces/HR/pages/1"},
    }
    monkeypatch.setattr("app.sources.confluence.httpx.get", lambda *a, **kw: _FakeResponse(payload))
    from app.config.settings import ConfluenceCredential

    adapter = ConfluenceAdapter(
        credential=ConfluenceCredential("https://acme.atlassian.net/wiki", "you@acme.com", "tok-acme")
    )
    doc = adapter.fetch_document("1")
    assert "Permissible expenses include gym." in doc.content
    assert doc.source_uri == "https://acme.atlassian.net/wiki/spaces/HR/pages/1"


def test_factory_wires_confluence(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_BASE_URL_ACME", "https://acme.atlassian.net/wiki")
    monkeypatch.setenv("CONFLUENCE_EMAIL_ACME", "you@acme.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN_ACME", "tok-acme")
    adapter = build_source_adapter("confluence", token_name="acme")
    assert isinstance(adapter, ConfluenceAdapter)
    assert adapter._credential.token == "tok-acme"
