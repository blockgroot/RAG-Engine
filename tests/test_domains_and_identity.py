"""Phase 13 (simplified auth): domain allowlist, users, magic-link tokens, sessions.

DB-backed (requires_db). No DNS involved — a domain is live for auto-join the
moment an admin registers it.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth import domains as domains_mod
from app.auth.session import create_session_token, decode_session_token
from app.auth.users import ROLE_ADMIN, create_admin, create_user, get_or_create_member
from app.core.exceptions import AuthError, ConfigurationError

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret")


@requires_db
def test_register_domain_rejects_public_provider(store, org_cleanup):
    org_id = store.create_organization("Domain Test Org")
    org_cleanup.append(org_id)
    with pytest.raises(ConfigurationError):
        domains_mod.register_domain(org_id, "gmail.com")


@requires_db
def test_register_domain_is_live_immediately(store, org_cleanup):
    org_id = store.create_organization("Domain Test Org 2")
    org_cleanup.append(org_id)
    domain = f"register-{uuid.uuid4().hex[:8]}.example.com"

    record = domains_mod.register_domain(org_id, domain)
    assert record.auto_join_enabled is True
    assert domains_mod.resolve_org_for_email(f"alice@{domain}") == org_id


@requires_db
def test_auto_join_can_be_disabled(store, org_cleanup):
    org_id = store.create_organization("Domain Test Org 3")
    org_cleanup.append(org_id)
    domain = f"disable-{uuid.uuid4().hex[:8]}.example.com"
    record = domains_mod.register_domain(org_id, domain)

    ok = domains_mod.set_auto_join(org_id, record.id, False)
    assert ok is True
    assert domains_mod.resolve_org_for_email(f"alice@{domain}") is None


@requires_db
def test_set_auto_join_rejects_another_orgs_domain(store, org_cleanup):
    org_a = store.create_organization("Domain Test Org 4a")
    org_b = store.create_organization("Domain Test Org 4b")
    org_cleanup.extend([org_a, org_b])
    domain = f"cross-{uuid.uuid4().hex[:8]}.example.com"
    record = domains_mod.register_domain(org_a, domain)

    ok = domains_mod.set_auto_join(org_b, record.id, False)
    assert ok is False  # org_b doesn't own this domain


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
