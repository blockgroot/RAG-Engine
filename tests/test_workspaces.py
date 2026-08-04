"""app/workspaces: sub-workspace CRUD + membership (Workspace-within-a-Workspace).

DB-backed (requires_db) — membership/isolation can only be proven against a
real Postgres instance, same reasoning as tests/test_isolation.py.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth.users import create_admin, invite_member as invite_org_member
from app.core.exceptions import AuthError, NotFoundError
from app.workspaces import (
    assert_member,
    create_workspace,
    invite_member,
    list_my_workspaces,
    list_workspace_members,
)

from .conftest import requires_db


def _email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


@requires_db
def test_create_workspace_adds_creator_as_owner(store, org_cleanup):
    org_id = store.create_organization("Workspace Test Org")
    org_cleanup.append(org_id)
    creator = create_admin(_email("creator"), org_id)

    workspace_id = create_workspace(org_id, "Meeting Notes", creator.id)

    role = assert_member(workspace_id, org_id, creator.id)
    assert role == "owner"
    members = list_workspace_members(workspace_id)
    assert len(members) == 1
    assert members[0]["email"] == creator.email
    assert members[0]["role"] == "owner"


@requires_db
def test_invite_member_requires_existing_same_org_user(store, org_cleanup):
    org_id = store.create_organization("Workspace Invite Org")
    org_cleanup.append(org_id)
    owner = create_admin(_email("owner"), org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)

    colleague = invite_org_member(_email("colleague"), org_id)
    invite_member(workspace_id, org_id, owner.id, colleague.email)

    role = assert_member(workspace_id, org_id, colleague.id)
    assert role == "member"


@requires_db
def test_invite_member_rejects_unknown_email(store, org_cleanup):
    org_id = store.create_organization("Workspace Invite Unknown Org")
    org_cleanup.append(org_id)
    owner = create_admin(_email("owner"), org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)

    with pytest.raises(NotFoundError):
        invite_member(workspace_id, org_id, owner.id, "not-a-real-user@example.com")


@requires_db
def test_invite_member_rejects_user_from_a_different_org(store, org_cleanup):
    org_a = store.create_organization("Workspace Cross Org A")
    org_b = store.create_organization("Workspace Cross Org B")
    org_cleanup.extend([org_a, org_b])
    owner_a = create_admin(_email("owner-a"), org_a)
    outsider = create_admin(_email("outsider-b"), org_b)
    workspace_id = create_workspace(org_a, "Meeting Notes", owner_a.id)

    with pytest.raises(NotFoundError):
        invite_member(workspace_id, org_a, owner_a.id, outsider.email)


@requires_db
def test_assert_member_rejects_non_member(store, org_cleanup):
    org_id = store.create_organization("Workspace Non-Member Org")
    org_cleanup.append(org_id)
    owner = create_admin(_email("owner"), org_id)
    stranger = create_admin(_email("stranger"), org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)

    with pytest.raises(AuthError):
        assert_member(workspace_id, org_id, stranger.id)


@requires_db
def test_assert_member_rejects_member_of_a_different_workspace(store, org_cleanup):
    org_id = store.create_organization("Workspace Sibling Org")
    org_cleanup.append(org_id)
    owner_a = create_admin(_email("owner-a"), org_id)
    owner_b = create_admin(_email("owner-b"), org_id)
    workspace_a = create_workspace(org_id, "Workspace A", owner_a.id)
    create_workspace(org_id, "Workspace B", owner_b.id)

    with pytest.raises(AuthError):
        assert_member(workspace_a, org_id, owner_b.id)


@requires_db
def test_assert_member_rejects_mismatched_org_id(store, org_cleanup):
    org_a = store.create_organization("Workspace Forged Org A")
    org_b = store.create_organization("Workspace Forged Org B")
    org_cleanup.extend([org_a, org_b])
    owner = create_admin(_email("owner"), org_a)
    workspace_id = create_workspace(org_a, "Meeting Notes", owner.id)

    # A real workspace in org_a, probed with org_b as the caller's session org —
    # must fail closed even though owner.id/workspace_id are both otherwise valid.
    with pytest.raises(AuthError):
        assert_member(workspace_id, org_b, owner.id)


@requires_db
def test_list_my_workspaces_scoped_to_membership_and_org(store, org_cleanup):
    org_a = store.create_organization("Workspace List Org A")
    org_b = store.create_organization("Workspace List Org B")
    org_cleanup.extend([org_a, org_b])
    user_a = create_admin(_email("user-a"), org_a)
    user_b = create_admin(_email("user-b"), org_b)
    ws_a1 = create_workspace(org_a, "A Workspace 1", user_a.id)
    create_workspace(org_a, "A Workspace 2 (not joined)", user_b.id)  # different creator
    create_workspace(org_b, "B Workspace", user_b.id)

    workspaces = list_my_workspaces(org_a, user_a.id)

    assert [w.id for w in workspaces] == [ws_a1]
    assert workspaces[0].role == "owner"
