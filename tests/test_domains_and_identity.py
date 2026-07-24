"""Phase 13: domain verification/auto-join, users, magic-link tokens, sessions.

DB-backed (requires_db). DNS resolution itself is monkeypatched — no network —
but the HMAC expected-value computation and the DB state machine are real.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth import domains as domains_mod
from app.auth.session import create_session_token, decode_session_token
from app.auth.users import ROLE_ADMIN, create_admin, create_user, get_or_create_member
from app.config.settings import AuthSettings
from app.core.exceptions import AuthError, ConfigurationError

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret")


def _fake_txt_answer(value: str):
    class _Rdata:
        strings = [value.encode()]

    return [_Rdata()]


@requires_db
def test_register_domain_rejects_public_provider(store, org_cleanup):
    org_id = store.create_organization("Domain Test Org")
    org_cleanup.append(org_id)
    with pytest.raises(ConfigurationError):
        domains_mod.register_domain(org_id, "gmail.com")


@requires_db
def test_verify_domain_succeeds_when_dns_matches(store, org_cleanup, monkeypatch):
    org_id = store.create_organization("Domain Test Org 2")
    org_cleanup.append(org_id)
    domain = f"verify-{uuid.uuid4().hex[:8]}.example.com"

    instructions = domains_mod.register_domain(org_id, domain)
    monkeypatch.setattr(
        domains_mod.dns.resolver,
        "resolve",
        lambda host, kind: _fake_txt_answer(instructions.dns_record_value),
    )

    assert domains_mod.verify_domain(org_id, instructions.domain_id) is True
    (record,) = domains_mod.list_domains(org_id)
    assert record.verified_at is not None


@requires_db
def test_verify_domain_fails_when_dns_does_not_match(store, org_cleanup, monkeypatch):
    org_id = store.create_organization("Domain Test Org 3")
    org_cleanup.append(org_id)
    domain = f"verify-{uuid.uuid4().hex[:8]}.example.com"

    instructions = domains_mod.register_domain(org_id, domain)
    monkeypatch.setattr(
        domains_mod.dns.resolver, "resolve", lambda host, kind: _fake_txt_answer("wrong-value")
    )

    assert domains_mod.verify_domain(org_id, instructions.domain_id) is False
    (record,) = domains_mod.list_domains(org_id)
    assert record.verified_at is None


@requires_db
def test_auto_join_cannot_be_enabled_before_verification(store, org_cleanup):
    org_id = store.create_organization("Domain Test Org 4")
    org_cleanup.append(org_id)
    domain = f"verify-{uuid.uuid4().hex[:8]}.example.com"
    instructions = domains_mod.register_domain(org_id, domain)

    enabled = domains_mod.set_auto_join(org_id, instructions.domain_id, True)
    assert enabled is False  # rejected: not verified yet

    (record,) = domains_mod.list_domains(org_id)
    assert record.auto_join_enabled is False


@requires_db
def test_resolve_org_for_email_requires_both_verified_and_auto_join(store, org_cleanup):
    org_id = store.create_organization("Domain Test Org 5")
    org_cleanup.append(org_id)
    domain = f"resolve-{uuid.uuid4().hex[:8]}.example.com"
    instructions = domains_mod.register_domain(org_id, domain)

    # Verified but auto-join not yet enabled -> no resolution.
    from app.db.connection import get_connection

    with get_connection() as conn:
        conn.execute(
            "UPDATE org_domains SET verified_at = now() WHERE id = %s", (instructions.domain_id,)
        )
    assert domains_mod.resolve_org_for_email(f"alice@{domain}") is None

    domains_mod.set_auto_join(org_id, instructions.domain_id, True)
    assert domains_mod.resolve_org_for_email(f"alice@{domain}") == org_id


@requires_db
def test_get_or_create_member_does_not_move_existing_user_to_a_new_org(store, org_cleanup):
    org_a = store.create_organization("Domain Test Org 6a")
    org_b = store.create_organization("Domain Test Org 6b")
    org_cleanup.extend([org_a, org_b])
    email = f"pinned-{uuid.uuid4().hex[:8]}@example.com"

    first = get_or_create_member(email, org_a)
    second = get_or_create_member(email, org_b)  # different org resolved this time

    assert first.id == second.id
    assert second.org_id == org_a  # unchanged, not moved to org_b


@requires_db
def test_session_round_trip_carries_org_and_role(store, org_cleanup):
    org_id = store.create_organization("Session Test Org")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)

    token = create_session_token(admin)
    claims = decode_session_token(token)

    assert claims.user_id == admin.id
    assert claims.org_id == org_id
    assert claims.role == ROLE_ADMIN


def test_session_cannot_be_issued_for_user_with_no_org():
    user = create_user_stub()
    with pytest.raises(AuthError):
        create_session_token(user)


def create_user_stub():
    from app.auth.users import User
    from datetime import datetime, timezone

    return User(id="fake-id", email="nobody@example.com", org_id=None, role="member",
                created_at=datetime.now(timezone.utc))


def test_session_decode_rejects_tampered_token():
    user = create_admin_stub()
    token = create_session_token(user) + "tampered"
    with pytest.raises(AuthError):
        decode_session_token(token)


def create_admin_stub():
    from app.auth.users import User
    from datetime import datetime, timezone

    return User(id="fake-admin-id", email="admin@example.com", org_id="fake-org-id",
                role="admin", created_at=datetime.now(timezone.utc))
