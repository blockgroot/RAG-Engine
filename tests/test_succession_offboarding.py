"""Admin succession + offboarding: live role, promote/demote, workspace owner."""

from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth import (
    create_admin,
    create_session_token,
    demote_to_member,
    invite_member,
    promote_to_admin,
    remove_member,
)
from app.auth.session import decode_session_token
from app.core.exceptions import AuthError
from app.db.connection import get_connection
from app.workspaces import create_workspace, invite_member as invite_ws_member, make_workspace_owner

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("EMAIL_SENDER", "console")
    monkeypatch.setenv("FRONTEND_URL", "https://portal.example.com")
    monkeypatch.setenv("API_CORS_ORIGINS", "https://portal.example.com")


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


@pytest.fixture
def admin_org(store, org_cleanup):
    org_id = store.create_organization(f"Succession Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    cookies = {"session": create_session_token(admin)}
    return org_id, admin, cookies


@requires_db
def test_get_session_uses_live_role_after_promote(client, admin_org):
    """JWT may still say member; require_admin must see the DB role."""
    org_id, admin, cookies = admin_org
    member = invite_member(f"member-{uuid.uuid4().hex[:8]}@example.com", org_id)
    member_token = create_session_token(member)
    claims = decode_session_token(member_token)
    assert claims.role == "member"

    assert client.get("/admin/members", cookies={"session": member_token}).status_code == 403

    promote_to_admin(member.id, org_id)

    # Same cookie (JWT still encodes member) — live re-read unlocks admin.
    assert decode_session_token(member_token).role == "member"
    assert client.get("/admin/members", cookies={"session": member_token}).status_code == 200
    me = client.get("/me", cookies={"session": member_token}).json()
    assert me["role"] == "admin"


@requires_db
def test_demote_revokes_sessions_and_blocks_last_admin(client, admin_org):
    org_id, admin, cookies = admin_org
    second = invite_member(f"second-{uuid.uuid4().hex[:8]}@example.com", org_id)
    promote_to_admin(second.id, org_id)
    second_token = create_session_token(second)

    assert client.get("/admin/members", cookies={"session": second_token}).status_code == 200

    demoted = client.post(f"/admin/members/{second.id}/demote", cookies=cookies)
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "member"

    # Demote sets sessions_revoked_at — old cookie is rejected entirely.
    assert client.get("/me", cookies={"session": second_token}).status_code == 401

    # Last admin cannot be demoted.
    alone = client.post(f"/admin/members/{admin.id}/demote", cookies=cookies)
    assert alone.status_code == 400
    assert "last admin" in alone.json()["detail"].lower()


@requires_db
def test_promote_via_api_and_cannot_remove_last_admin(client, admin_org):
    org_id, admin, cookies = admin_org
    member = invite_member(f"member-{uuid.uuid4().hex[:8]}@example.com", org_id)

    promoted = client.post(f"/admin/members/{member.id}/promote", cookies=cookies)
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    # With two admins, removing one is fine.
    removed = client.delete(f"/admin/members/{member.id}", cookies=cookies)
    assert removed.status_code == 200

    # Sole admin cannot be removed by… nobody else can; self-remove blocked.
    self_rm = client.delete(f"/admin/members/{admin.id}", cookies=cookies)
    assert self_rm.status_code == 400


@requires_db
def test_remove_blocks_sole_workspace_owner_with_other_members(client, admin_org, store):
    org_id, admin, cookies = admin_org
    owner = invite_member(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    colleague = invite_member(f"colleague-{uuid.uuid4().hex[:8]}@example.com", org_id)

    workspace_id = create_workspace(org_id, "Notes", owner.id)
    invite_ws_member(workspace_id, org_id, owner.id, colleague.email)

    blocked = client.delete(f"/admin/members/{owner.id}", cookies=cookies)
    assert blocked.status_code == 400
    assert "only owner" in blocked.json()["detail"].lower()

    # Transfer ownership, then remove succeeds.
    make_workspace_owner(workspace_id, org_id, owner.id, colleague.id)
    ok = client.delete(f"/admin/members/{owner.id}", cookies=cookies)
    assert ok.status_code == 200

    with get_connection() as conn:
        still = conn.execute(
            "SELECT 1 FROM users WHERE id = %s::uuid", (owner.id,)
        ).fetchone()
    assert still is None


@requires_db
def test_remove_deletes_solo_owned_empty_workspace(store, org_cleanup):
    org_id = store.create_organization(f"Solo Space Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    owner = invite_member(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Only Mine", owner.id)

    remove_member(owner.id, org_id, acting_user_id=admin.id)

    with get_connection() as conn:
        gone = conn.execute(
            "SELECT 1 FROM workspaces WHERE id = %s::uuid", (workspace_id,)
        ).fetchone()
    assert gone is None


@requires_db
def test_make_workspace_owner_api(client, admin_org):
    org_id, admin, cookies = admin_org
    # Admin creates a space and invites a colleague, then promotes them.
    workspace_id = (
        client.post("/workspaces", json={"name": "Handoff"}, cookies=cookies).json()["id"]
    )
    colleague = invite_member(f"colleague-{uuid.uuid4().hex[:8]}@example.com", org_id)
    invite = client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": colleague.email},
        cookies=cookies,
    )
    assert invite.status_code == 200

    made = client.post(
        f"/workspaces/{workspace_id}/members/{colleague.id}/make-owner",
        cookies=cookies,
    )
    assert made.status_code == 200

    members = client.get(f"/workspaces/{workspace_id}/members", cookies=cookies).json()
    by_email = {m["email"]: m for m in members}
    assert by_email[colleague.email]["role"] == "owner"
    assert "user_id" in by_email[colleague.email]


@requires_db
def test_demote_helper_blocks_last_admin(store, org_cleanup):
    org_id = store.create_organization(f"Last Admin Demote {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"solo-{uuid.uuid4().hex[:8]}@example.com", org_id)
    with pytest.raises(AuthError, match="last admin"):
        demote_to_member(admin.id, org_id)
