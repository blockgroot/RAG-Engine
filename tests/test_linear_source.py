"""LinearAdapter tests: pagination, issue->text flattening, per-org token
resolution. No network — httpx.post is monkeypatched to return canned
GraphQL responses.
"""

from __future__ import annotations

import pytest

from app.config.settings import LinearSettings
from app.core.exceptions import ConfigurationError, SourceError
from app.sources import build_source_adapter
from app.sources.linear import LinearAdapter


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_resolve_token_never_falls_back(monkeypatch):
    monkeypatch.setenv("LINEAR_TOKEN", "GLOBAL-DEFAULT")
    monkeypatch.setenv("LINEAR_TOKEN_ACME", "tok-acme")
    settings = LinearSettings.from_env()
    assert settings.resolve_token("acme") == "tok-acme"
    with pytest.raises(ConfigurationError):
        settings.resolve_token("globex")


def test_list_documents_paginates(monkeypatch):
    pages = [
        {
            "data": {
                "issues": {
                    "nodes": [{"id": "1", "title": "A", "url": "u1", "updatedAt": "2026-01-01T00:00:00Z"}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                }
            }
        },
        {
            "data": {
                "issues": {
                    "nodes": [{"id": "2", "title": "B", "url": "u2", "updatedAt": "2026-01-02T00:00:00Z"}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        },
    ]

    def fake_post(url, json, headers, timeout):
        assert headers["Authorization"] == "tok-acme"
        return _FakeResponse(pages.pop(0))

    monkeypatch.setattr("app.sources.linear.httpx.post", fake_post)
    adapter = LinearAdapter(token="tok-acme")
    refs = adapter.list_documents()
    assert [r.external_id for r in refs] == ["1", "2"]


def test_fetch_document_flattens_description_and_comments(monkeypatch):
    payload = {
        "data": {
            "issue": {
                "id": "1",
                "title": "Bug",
                "url": "u1",
                "updatedAt": "2026-01-01T00:00:00Z",
                "description": "It is broken.",
                "comments": {"nodes": [{"body": "confirmed", "user": {"name": "Ana"}, "createdAt": "x"}]},
            }
        }
    }
    monkeypatch.setattr("app.sources.linear.httpx.post", lambda *a, **kw: _FakeResponse(payload))
    adapter = LinearAdapter(token="tok-acme")
    doc = adapter.fetch_document("1")
    assert "It is broken." in doc.content
    assert "Ana commented: confirmed" in doc.content


def test_graphql_errors_raise_source_error(monkeypatch):
    monkeypatch.setattr(
        "app.sources.linear.httpx.post",
        lambda *a, **kw: _FakeResponse({"errors": [{"message": "bad token"}]}),
    )
    adapter = LinearAdapter(token="tok-acme")
    with pytest.raises(SourceError):
        adapter.list_documents()


def test_factory_wires_linear(monkeypatch):
    monkeypatch.setenv("LINEAR_TOKEN_ACME", "tok-acme")
    adapter = build_source_adapter("linear", token_name="acme")
    assert isinstance(adapter, LinearAdapter)
    assert adapter._token == "tok-acme"
    assert adapter._oauth is False


def test_factory_marks_a_directly_passed_token_as_oauth(monkeypatch):
    """A `token=` (from get_live_connection_token) is the OAuth-connected
    path — Linear needs a `Bearer` prefix for this token but not for a
    personal API key, so the factory must flag it correctly."""
    adapter = build_source_adapter("linear", token="oauth-token-abc")
    assert isinstance(adapter, LinearAdapter)
    assert adapter._token == "oauth-token-abc"
    assert adapter._oauth is True


def test_oauth_token_sends_bearer_prefix(monkeypatch):
    payload = {
        "data": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}
    }

    def fake_post(url, json, headers, timeout):
        assert headers["Authorization"] == "Bearer oauth-token-abc"
        return _FakeResponse(payload)

    monkeypatch.setattr("app.sources.linear.httpx.post", fake_post)
    adapter = LinearAdapter(token="oauth-token-abc", oauth=True)
    adapter.list_documents()


def test_personal_key_sends_raw_no_prefix(monkeypatch):
    payload = {
        "data": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}
    }

    def fake_post(url, json, headers, timeout):
        assert headers["Authorization"] == "tok-acme"
        return _FakeResponse(payload)

    monkeypatch.setattr("app.sources.linear.httpx.post", fake_post)
    adapter = LinearAdapter(token="tok-acme")
    adapter.list_documents()
