"""A self-hosted copy (SIGNUP_ENABLED=false) takes no public sign-ups.

Its one company is created at install; a sign-up form left open would let
anyone who finds the address queue another company on the customer's server.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import app


def test_signup_is_refused_when_off(monkeypatch):
    monkeypatch.setenv("SIGNUP_ENABLED", "false")
    response = TestClient(app).post(
        "/auth/signup", json={"email": "new@example.com", "company_name": "New Co"}
    )
    assert response.status_code == 403
    assert "invite" in response.json()["detail"]


def test_an_unknown_email_is_not_offered_signup_when_off(monkeypatch):
    monkeypatch.setenv("SIGNUP_ENABLED", "false")
    monkeypatch.setattr("app.api.auth.get_user_by_email", lambda email: None)
    monkeypatch.setattr("app.api.auth.check_rate_limit", lambda *a, **k: None)
    response = TestClient(app).post("/auth/magic-link", json={"email": "who@example.com"})
    body = response.json()
    assert body["status"] == "no_account"
    assert body["signup_enabled"] is False
    assert "invite" in body["message"]
