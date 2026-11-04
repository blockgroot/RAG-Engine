"""Phase 21: rate limiting, session revocation, ingestion sanitization."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config.settings import IngestSanitizeSettings
from app.core.exceptions import ProviderError
from app.ingestion.sanitize import sanitize_ingest_text

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("EMAIL_SENDER", "console")
    monkeypatch.setenv("FRONTEND_URL", "https://portal.example.com")
    monkeypatch.setenv("API_CORS_ORIGINS", "https://portal.example.com")


def test_sanitize_strips_nul_and_accepts_normal_text():
    out = sanitize_ingest_text("Hello\x00policy\n\nLine two.")
    assert "\x00" not in out
    assert "Hello" in out


def test_sanitize_rejects_oversized_document():
    settings = IngestSanitizeSettings(max_document_chars=100, max_control_char_ratio=0.05)
    with pytest.raises(ProviderError, match="maximum size"):
        sanitize_ingest_text("x" * 101, settings=settings)


def test_sanitize_rejects_mostly_control_chars():
    settings = IngestSanitizeSettings(max_document_chars=10_000, max_control_char_ratio=0.01)
    bad = "\x01" * 50 + "ok"
    with pytest.raises(ProviderError, match="malformed"):
        sanitize_ingest_text(bad, settings=settings)


@pytest.fixture
def admin_org(store, org_cleanup):
    from app.auth import create_admin, create_session_token

    org_id = store.create_organization(f"SecTest Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    token = create_session_token(admin)
    return org_id, {"session": token}


@requires_db
def test_revoked_session_rejected(admin_org):
    from app.api.main import create_app
    from app.auth import create_session_token, invite_member

    org_id, admin_cookies = admin_org
    member = invite_member(f"member-{uuid.uuid4().hex[:8]}@example.com", org_id)
    member_token = create_session_token(member)

    app = create_app()
    c = TestClient(app)
    assert c.get("/admin/members", cookies=admin_cookies).status_code == 200
    assert c.get("/admin/members", cookies={"session": member_token}).status_code == 403

    revoke = c.post(
        f"/admin/members/{member.id}/revoke-sessions",
        cookies=admin_cookies,
    )
    assert revoke.status_code == 200

    assert c.get("/admin/members", cookies={"session": member_token}).status_code == 401


@requires_db
def test_chat_rate_limit_returns_429(store, org_cleanup, monkeypatch):
    from app.api.main import create_app
    from app.auth import create_admin, create_session_token
    from tests.test_api_chat import _fake_agent

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_QUESTION_BURST", "2")
    monkeypatch.setenv("RATE_LIMIT_QUESTIONS_PER_MINUTE", "1")
    monkeypatch.setenv("CHAT_STREAM_WORD_DELAY_MS", "0")

    org_id = store.create_organization(f"RateLimitOrg-{uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    user = create_admin(f"rl-{uuid.uuid4().hex[:8]}@example.com", org_id)
    cookies = {"session": create_session_token(user)}

    app = create_app()
    fake = _fake_agent(org_id)
    monkeypatch.setattr("app.api.chat.get_policy_agent", lambda: fake)
    client = TestClient(app)

    for _ in range(2):
        r = client.post(
            "/chat/stream",
            json={"question": "How many leave days?"},
            cookies=cookies,
        )
        assert r.status_code == 200
    r = client.post(
        "/chat/stream",
        json={"question": "How many leave days?"},
        cookies=cookies,
    )
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    assert "try again in" in r.json()["detail"]
