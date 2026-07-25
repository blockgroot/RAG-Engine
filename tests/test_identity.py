"""Phase 13 (simplified auth): users, invites, magic-link tokens, sessions.

DB-backed (requires_db). Domain-based auto-join was removed in favor of direct
admin invites — see CLAUDE.md §2/§4. This file replaces the old
test_domains_and_identity.py.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth.session import create_session_token, decode_session_token
from app.auth.users import ROLE_ADMIN, create_admin, invite_member, list_members
from app.core.exceptions import AuthError

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret")


@requires_db
def test_invite_member_scopes_directly_to_org_no_domain_matching(store, org_cleanup):
    org_id = store.create_organization("Invite Test Org")
    org_cleanup.append(org_id)
    email = f"invited-{uuid.uuid4().hex[:8]}@anydomain.example"

    user = invite_member(email, org_id)

    assert user.org_id == org_id
    assert user.role == "member"


@requires_db
def test_list_members_scoped_to_own_org(store, org_cleanup):
    org_a = store.create_organization("Invite Test Org A")
    org_b = store.create_organization("Invite Test Org B")
    org_cleanup.extend([org_a, org_b])
    create_admin(f"admin-a-{uuid.uuid4().hex[:8]}@example.com", org_a)
    invite_member(f"member-a-{uuid.uuid4().hex[:8]}@example.com", org_a)
    invite_member(f"member-b-{uuid.uuid4().hex[:8]}@example.com", org_b)

    members_a = list_members(org_a)
    assert len(members_a) == 2
    assert all(m.org_id == org_a for m in members_a)


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
