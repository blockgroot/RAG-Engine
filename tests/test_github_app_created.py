"""GitHub returns a self-hosted admin to Handbook after creating the App.

Handbook exchanges the one-time code and shows the four .env lines, so the
admin never copies a code out of the address bar into a command. Only an
admin sees them, the code is checked before it reaches a URL, and the page is
never cached. GitHub is faked.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import auth
from app.api.main import app

URL = "/auth/github/app-created"
APP = {"slug": "hb-test", "name": "HB Test", "client_id": "Iv1.abc",
       "client_secret": "s3cret", "pem": "-----BEGIN KEY-----\nAAA\nBBB\n-----END KEY-----\n"}


@pytest.fixture
def github(monkeypatch):
    calls = []

    def post(url, **kw):
        calls.append(url)
        return httpx.Response(201, json=APP)

    monkeypatch.setattr(httpx, "post", post)
    return calls


def signed_in(monkeypatch, role):
    monkeypatch.setattr(auth, "get_session", lambda request: SimpleNamespace(role=role))


def test_an_admin_gets_the_env_lines(monkeypatch, github):
    signed_in(monkeypatch, "admin")
    response = TestClient(app).get(URL, params={"code": "abc123"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert github == ["https://api.github.com/app-manifests/abc123/conversions"]
    assert "GITHUB_CLIENT_ID=Iv1.abc" in response.text
    assert "GITHUB_APP_PRIVATE_KEY=-----BEGIN KEY-----\\nAAA\\nBBB\\n-----END KEY-----" in response.text


def test_a_member_sees_nothing(monkeypatch, github):
    signed_in(monkeypatch, "member")
    assert TestClient(app).get(URL, params={"code": "abc123"}).status_code == 403
    assert github == []


def test_signed_out_is_asked_to_sign_in(monkeypatch, github):
    def no_session(request):
        raise HTTPException(status_code=401)

    monkeypatch.setattr(auth, "get_session", no_session)
    assert TestClient(app).get(URL, params={"code": "abc123"}).status_code == 401
    assert github == []


def test_a_strange_code_never_reaches_github(monkeypatch, github):
    signed_in(monkeypatch, "admin")
    assert TestClient(app).get(URL, params={"code": "../users"}).status_code == 400
    assert github == []
