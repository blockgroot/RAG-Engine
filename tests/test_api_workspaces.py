"""Task 9: /workspaces HTTP API (create, invite, connections, jobs).

Focus: every route requires session auth; every route on an EXISTING
workspace additionally requires membership (and owner role where the plan
restricts it), and a workspace from one org must never resolve for a
different org's session, even by guessing its id -- the single most
important property this file proves.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import OAuthTokens, create_admin, create_session_token, save_connection
from app.db.connection import get_connection
from app.jobs import enqueue, mark_succeeded
from app.auth.users import invite_member as invite_org_member

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    from cryptography.fernet import Fernet

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
def owner_org(store, org_cleanup):
    """(org_id, owner_user, session_cookie_dict) for a fresh org's admin."""
    org_id = store.create_organization(f"Workspace API Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    token = create_session_token(owner)
    return org_id, owner, {"session": token}


@requires_db
def test_create_workspace_and_creator_is_owner(client, owner_org):
    org_id, owner, cookies = owner_org
    response = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=cookies)
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "owner"
    workspace_id = body["id"]

    members = client.get(f"/workspaces/{workspace_id}/members", cookies=cookies)
    assert members.status_code == 200
    assert [m["email"] for m in members.json()] == [owner.email]


@requires_db
def test_list_mine_only_returns_workspaces_i_am_a_member_of(client, store, org_cleanup):
    org_id = store.create_organization(f"Workspace List Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    alice = create_admin(f"alice-{uuid.uuid4().hex[:8]}@example.com", org_id)
    bob = create_admin(f"bob-{uuid.uuid4().hex[:8]}@example.com", org_id)
    alice_cookies = {"session": create_session_token(alice)}
    bob_cookies = {"session": create_session_token(bob)}

    client.post("/workspaces", json={"name": "Alice's Workspace"}, cookies=alice_cookies)

    alice_list = client.get("/workspaces", cookies=alice_cookies)
    bob_list = client.get("/workspaces", cookies=bob_cookies)
    assert len(alice_list.json()) == 1
    assert bob_list.json() == []


@requires_db
def test_invite_requires_owner_role(client, store, org_cleanup):
    org_id = store.create_organization(f"Workspace Invite Role Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    owner_cookies = {"session": create_session_token(owner)}
    colleague = invite_org_member(f"colleague-{uuid.uuid4().hex[:8]}@example.com", org_id)

    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=owner_cookies)
    workspace_id = create_resp.json()["id"]
    client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": colleague.email},
        cookies=owner_cookies,
    )

    colleague_cookies = {"session": create_session_token(colleague)}
    second_invite = invite_org_member(f"second-{uuid.uuid4().hex[:8]}@example.com", org_id)
    response = client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": second_invite.email},
        cookies=colleague_cookies,
    )
    assert response.status_code == 403


@requires_db
def test_invite_rejects_email_outside_the_org(client, owner_org):
    org_id, owner, cookies = owner_org
    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=cookies)
    workspace_id = create_resp.json()["id"]

    response = client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": "not-in-this-org@example.com"},
        cookies=cookies,
    )
    assert response.status_code == 404


@requires_db
def test_non_member_gets_403_on_members_route(client, store, org_cleanup):
    org_id = store.create_organization(f"Workspace Non-Member Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    stranger = create_admin(f"stranger-{uuid.uuid4().hex[:8]}@example.com", org_id)
    owner_cookies = {"session": create_session_token(owner)}
    stranger_cookies = {"session": create_session_token(stranger)}

    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=owner_cookies)
    workspace_id = create_resp.json()["id"]

    response = client.get(f"/workspaces/{workspace_id}/members", cookies=stranger_cookies)
    assert response.status_code == 403


@requires_db
def test_workspace_from_another_org_never_resolves(client, store, org_cleanup):
    """The critical cross-tenant proof: org B's admin, given org A's real
    workspace id, must get 403 -- never 200 with data, never a 500."""
    org_a = store.create_organization(f"Workspace Cross Org A {uuid.uuid4().hex[:8]}")
    org_b = store.create_organization(f"Workspace Cross Org B {uuid.uuid4().hex[:8]}")
    org_cleanup.extend([org_a, org_b])
    owner_a = create_admin(f"owner-a-{uuid.uuid4().hex[:8]}@example.com", org_a)
    admin_b = create_admin(f"admin-b-{uuid.uuid4().hex[:8]}@example.com", org_b)
    owner_a_cookies = {"session": create_session_token(owner_a)}
    admin_b_cookies = {"session": create_session_token(admin_b)}

    create_resp = client.post(
        "/workspaces", json={"name": "Org A Meeting Notes"}, cookies=owner_a_cookies
    )
    workspace_id = create_resp.json()["id"]

    for path in (
        f"/workspaces/{workspace_id}/members",
        f"/workspaces/{workspace_id}/connections",
        f"/workspaces/{workspace_id}/jobs",
    ):
        response = client.get(path, cookies=admin_b_cookies)
        assert response.status_code == 403, f"{path} leaked across orgs: {response.status_code}"


@requires_db
def test_workspaces_route_requires_a_session(client, owner_org):
    org_id, owner, cookies = owner_org
    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=cookies)
    workspace_id = create_resp.json()["id"]

    response = client.get(f"/workspaces/{workspace_id}/members")
    assert response.status_code == 401


# -- Drive folder-picker dropdown (search-as-you-type over an existing connection) --


def _save_workspace_google(org_id: str, workspace_id: str) -> str:
    return save_connection(
        org_id,
        "google",
        OAuthTokens(
            access_token="goog_access",
            refresh_token="goog_refresh",
            expires_at=None,
            external_workspace_id="drive-user@example.com",
            external_workspace_name="drive-user@example.com",
        ),
        workspace_id=workspace_id,
    )


@requires_db
def test_search_workspace_drive_folders_returns_matches(client, owner_org, monkeypatch):
    org_id, owner, cookies = owner_org
    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=cookies)
    workspace_id = create_resp.json()["id"]
    connection_id = _save_workspace_google(org_id, workspace_id)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"files": [{"id": "folder-1", "name": "Q3 Meeting Notes"}]}

    monkeypatch.setattr(
        "app.sources.google_drive_utils.httpx.get", lambda *a, **k: FakeResponse()
    )

    response = client.get(
        f"/workspaces/{workspace_id}/connections/{connection_id}/drive-folders",
        params={"q": "Q3"},
        cookies=cookies,
    )
    assert response.status_code == 200
    assert response.json()["folders"] == [{"id": "folder-1", "name": "Q3 Meeting Notes"}]


@requires_db
def test_put_workspace_google_config_overwrites_existing_folder(
    client, owner_org, monkeypatch
):
    """Change folder: workspace owner can replace the linked Drive folder."""
    org_id, owner, cookies = owner_org
    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=cookies)
    workspace_id = create_resp.json()["id"]
    connection_id = _save_workspace_google(org_id, workspace_id)

    class FakeResponse:
        def __init__(self, folder_id: str, name: str):
            self.status_code = 200
            self._folder_id = folder_id
            self._name = name

        def json(self):
            return {
                "id": self._folder_id,
                "name": self._name,
                "mimeType": "application/vnd.google-apps.folder",
            }

    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResponse("ws-folder-old", "Old Notes")
        return FakeResponse("ws-folder-new", "New Notes")

    monkeypatch.setattr("app.sources.google_drive_utils.httpx.get", fake_get)

    first = client.put(
        f"/workspaces/{workspace_id}/connections/{connection_id}/config",
        json={"folder_url": "ws-folder-old"},
        cookies=cookies,
    )
    assert first.status_code == 200
    assert first.json()["config"]["folder_id"] == "ws-folder-old"

    second = client.put(
        f"/workspaces/{workspace_id}/connections/{connection_id}/config",
        json={"folder_url": "ws-folder-new"},
        cookies=cookies,
    )
    assert second.status_code == 200
    assert second.json()["config"]["folder_id"] == "ws-folder-new"
    assert second.json()["config"]["folder_name"] == "New Notes"

    listed = client.get(
        f"/workspaces/{workspace_id}/connections", cookies=cookies
    ).json()
    google = next(c for c in listed if c["id"] == connection_id)
    assert google["source_config"]["folder_id"] == "ws-folder-new"


@requires_db
def test_search_workspace_drive_folders_requires_owner_role(client, store, org_cleanup):
    org_id = store.create_organization(f"Workspace Drive Search Role Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    owner_cookies = {"session": create_session_token(owner)}
    colleague = invite_org_member(f"colleague-{uuid.uuid4().hex[:8]}@example.com", org_id)
    colleague_cookies = {"session": create_session_token(colleague)}

    create_resp = client.post("/workspaces", json={"name": "Meeting Notes"}, cookies=owner_cookies)
    workspace_id = create_resp.json()["id"]
    client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": colleague.email},
        cookies=owner_cookies,
    )
    connection_id = _save_workspace_google(org_id, workspace_id)

    response = client.get(
        f"/workspaces/{workspace_id}/connections/{connection_id}/drive-folders",
        cookies=colleague_cookies,
    )
    assert response.status_code == 403


@requires_db
def test_search_workspace_drive_folders_wrong_workspace_returns_404(client, owner_org):
    org_id, owner, cookies = owner_org
    workspace_a = client.post(
        "/workspaces", json={"name": "Workspace A"}, cookies=cookies
    ).json()["id"]
    workspace_b = client.post(
        "/workspaces", json={"name": "Workspace B"}, cookies=cookies
    ).json()["id"]
    connection_id = _save_workspace_google(org_id, workspace_a)

    response = client.get(
        f"/workspaces/{workspace_b}/connections/{connection_id}/drive-folders",
        cookies=cookies,
    )
    assert response.status_code == 404


# -- ready_to_ask gate (mirrors GET /me, scoped to the workspace) --


def _insert_workspace_document(org_id: str, workspace_id: str, title: str = "Notes") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO documents (org_id, title, workspace_id, source_provider, source_external_id) "
            "VALUES (%s, %s, %s, 'notion', %s)",
            (org_id, title, workspace_id, f"ext-{title}"),
        )


@requires_db
def test_workspace_detail_ready_to_ask_false_until_succeeded_sync(client, owner_org):
    org_id, owner, cookies = owner_org
    workspace_id = client.post(
        "/workspaces", json={"name": "Meeting Notes"}, cookies=cookies
    ).json()["id"]

    detail = client.get(f"/workspaces/{workspace_id}", cookies=cookies)
    assert detail.status_code == 200
    body = detail.json()
    assert body["ready_to_ask"] is False
    assert body["has_connection"] is False
    assert body["sync_in_progress"] is False

    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_ws",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-ready",
        ),
        workspace_id=workspace_id,
    )
    job_id = enqueue(org_id, connection_id, workspace_id=workspace_id)
    mid = client.get(f"/workspaces/{workspace_id}", cookies=cookies).json()
    assert mid["has_connection"] is True
    assert mid["sync_in_progress"] is True
    assert mid["ready_to_ask"] is False

    mark_succeeded(job_id, doc_count=1)
    _insert_workspace_document(org_id, workspace_id)
    ready = client.get(f"/workspaces/{workspace_id}", cookies=cookies).json()
    assert ready["ready_to_ask"] is True
    assert ready["has_documents"] is True
    assert ready["sync_in_progress"] is False
    assert ready["latest_job_status"] == "succeeded"


@requires_db
def test_org_me_ready_to_ask_ignores_workspace_only_sync(client, owner_org):
    """A workspace sync must not unlock (or block) org-wide Ask."""
    org_id, owner, cookies = owner_org
    workspace_id = client.post(
        "/workspaces", json={"name": "Personal Notes"}, cookies=cookies
    ).json()["id"]
    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_ws_only",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-only",
        ),
        workspace_id=workspace_id,
    )
    job_id = enqueue(org_id, connection_id, workspace_id=workspace_id)
    mark_succeeded(job_id, doc_count=2)
    _insert_workspace_document(org_id, workspace_id, title="Workspace Doc")

    me = client.get("/me", cookies=cookies).json()
    assert me["ready_to_ask"] is False
    assert me["has_connection"] is False
    assert me["has_documents"] is False
    assert me["sync_in_progress"] is False

    ws = client.get(f"/workspaces/{workspace_id}", cookies=cookies).json()
    assert ws["ready_to_ask"] is True


@requires_db
def test_a_later_sync_does_not_revoke_ready_to_ask(client, owner_org):
    """A re-sync must not lock an already-ingested workspace out of Ask.

    Regression from production: onboarding finished (11 pages / 68 chunks
    stored), then one extra queued job flipped ``ready_to_ask`` back to False
    and pinned the page on "Bringing your policies in…" with no way forward,
    even though every document was present and answerable. Re-syncing is the
    normal steady state for an existing org, and incremental sync only upserts
    changed pages — it never empties the corpus — so an in-flight sync is no
    reason to withdraw Ask. ``sync_in_progress`` stays True so the UI can show
    a passive indicator; it just must not gate.
    """
    org_id, owner, cookies = owner_org
    workspace_id = client.post(
        "/workspaces", json={"name": "Handbook"}, cookies=cookies
    ).json()["id"]
    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_resync",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-resync",
        ),
        workspace_id=workspace_id,
    )

    # First sync completes -> answerable.
    first = enqueue(org_id, connection_id, workspace_id=workspace_id)
    mark_succeeded(first, doc_count=11)
    _insert_workspace_document(org_id, workspace_id)
    ready = client.get(f"/workspaces/{workspace_id}", cookies=cookies).json()
    assert ready["ready_to_ask"] is True

    # A SECOND sync is queued (re-sync, or a redundant extra click).
    enqueue(org_id, connection_id, workspace_id=workspace_id)
    during = client.get(f"/workspaces/{workspace_id}", cookies=cookies).json()

    assert during["sync_in_progress"] is True, "the running sync is still reported"
    assert during["has_documents"] is True
    assert during["ready_to_ask"] is True, (
        "a later sync must NOT revoke Ask — the documents are still there; "
        "this is the production lockout regression"
    )


@requires_db
def test_first_sync_is_still_gated_until_it_succeeds(client, owner_org):
    """The guard the removed `not syncing` term was there for must still hold.

    Before any sync has ever succeeded there are no documents, so
    ``succeeded and docs`` is False on its own — no need to also test
    ``not syncing``.
    """
    org_id, owner, cookies = owner_org
    workspace_id = client.post(
        "/workspaces", json={"name": "Fresh"}, cookies=cookies
    ).json()["id"]
    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_fresh",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-fresh",
        ),
        workspace_id=workspace_id,
    )
    enqueue(org_id, connection_id, workspace_id=workspace_id)

    body = client.get(f"/workspaces/{workspace_id}", cookies=cookies).json()
    assert body["sync_in_progress"] is True
    assert body["has_documents"] is False
    assert body["ready_to_ask"] is False, "must not unlock before the first sync lands"


@requires_db
def test_owner_can_delete_workspace_and_cascades_scoped_docs(client, owner_org):
    org_id, owner, cookies = owner_org
    created = client.post("/workspaces", json={"name": "Temp Space"}, cookies=cookies).json()
    workspace_id = created["id"]

    # Org-wide doc must survive; workspace doc must go with the space.
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents (org_id, title, source_provider, source_external_id)
            VALUES (%s::uuid, 'Org Policy', 'notion', 'org-doc')
            """,
            (org_id,),
        )
        conn.execute(
            """
            INSERT INTO documents (org_id, title, source_provider, source_external_id, workspace_id)
            VALUES (%s::uuid, 'Space Notes', 'notion', 'ws-doc', %s::uuid)
            """,
            (org_id, workspace_id),
        )

    deleted = client.delete(f"/workspaces/{workspace_id}", cookies=cookies)
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"

    assert client.get(f"/workspaces/{workspace_id}", cookies=cookies).status_code == 403
    listed = client.get("/workspaces", cookies=cookies).json()
    assert all(w["id"] != workspace_id for w in listed)

    with get_connection() as conn:
        org_docs = conn.execute(
            "SELECT title FROM documents WHERE org_id = %s::uuid AND workspace_id IS NULL",
            (org_id,),
        ).fetchall()
        ws_docs = conn.execute(
            "SELECT 1 FROM documents WHERE workspace_id = %s::uuid",
            (workspace_id,),
        ).fetchall()
    assert [r[0] for r in org_docs] == ["Org Policy"]
    assert ws_docs == []


@requires_db
def test_member_cannot_delete_workspace(client, owner_org):
    org_id, owner, cookies = owner_org
    workspace_id = client.post(
        "/workspaces", json={"name": "Shared"}, cookies=cookies
    ).json()["id"]
    member = invite_org_member(f"member-{uuid.uuid4().hex[:8]}@example.com", org_id)
    client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": member.email},
        cookies=cookies,
    )
    member_cookies = {"session": create_session_token(member)}
    refused = client.delete(f"/workspaces/{workspace_id}", cookies=member_cookies)
    assert refused.status_code == 403
    # Space still exists for the owner.
    assert client.get(f"/workspaces/{workspace_id}", cookies=cookies).status_code == 200


@requires_db
def test_foreign_org_cannot_delete_workspace(client, owner_org, store, org_cleanup):
    org_id, owner, cookies = owner_org
    workspace_id = client.post(
        "/workspaces", json={"name": "Private"}, cookies=cookies
    ).json()["id"]

    other_org = store.create_organization(f"Other Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other_org)
    stranger = create_admin(f"stranger-{uuid.uuid4().hex[:8]}@example.com", other_org)
    stranger_cookies = {"session": create_session_token(stranger)}

    refused = client.delete(f"/workspaces/{workspace_id}", cookies=stranger_cookies)
    assert refused.status_code == 403
    assert client.get(f"/workspaces/{workspace_id}", cookies=cookies).status_code == 200


# --------------------------------------------------------------------------
# The permission contract the space panel relies on
# --------------------------------------------------------------------------


@requires_db
def test_a_member_can_read_the_people_list_but_not_change_it(
    client, store, org_cleanup
):
    """Everyone now lands on Ask when they open a space, with people and
    settings one click behind the space name (the Slack pattern). That panel
    shows the people list to EVERY member, so a non-owner member must be able
    to read it — and must be refused every action it only offers to owners.

    Both halves matter: if the read 403s the panel is broken for exactly the
    person it exists for, and if the writes succeed a member can quietly
    promote themselves.
    """
    org_id = store.create_organization(f"Space Panel Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    colleague = invite_org_member(f"mate-{uuid.uuid4().hex[:8]}@example.com", org_id)
    owner_cookies = {"session": create_session_token(owner)}
    member_cookies = {"session": create_session_token(colleague)}

    workspace_id = client.post(
        "/workspaces", json={"name": "Meeting notes"}, cookies=owner_cookies
    ).json()["id"]
    added = client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": colleague.email},
        cookies=owner_cookies,
    )
    assert added.status_code == 200, added.text

    # The read the panel opens with.
    listing = client.get(f"/workspaces/{workspace_id}/members", cookies=member_cookies)
    assert listing.status_code == 200, listing.text
    people = listing.json()
    by_email = {p["email"]: p["role"] for p in people}
    assert by_email[owner.email] == "owner"
    assert by_email[colleague.email] == "member"

    # The actions the panel hides from a member must ALSO be refused server
    # side — a hidden button is not an access control.
    assert (
        client.post(
            f"/workspaces/{workspace_id}/members",
            json={"email": owner.email},
            cookies=member_cookies,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/workspaces/{workspace_id}/members/{colleague.id}/make-owner",
            cookies=member_cookies,
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/workspaces/{workspace_id}/members/{owner.id}",
            cookies=member_cookies,
        ).status_code
        == 403
    )


@requires_db
def test_the_space_listing_tells_a_member_their_role(client, store, org_cleanup):
    """The panel decides what to offer from this role, so it has to be right:
    an owner mislabelled as a member loses every control they should have."""
    org_id = store.create_organization(f"Space Role Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    colleague = invite_org_member(f"mate-{uuid.uuid4().hex[:8]}@example.com", org_id)
    owner_cookies = {"session": create_session_token(owner)}
    member_cookies = {"session": create_session_token(colleague)}

    workspace_id = client.post(
        "/workspaces", json={"name": "Meeting notes"}, cookies=owner_cookies
    ).json()["id"]
    client.post(
        f"/workspaces/{workspace_id}/members",
        json={"email": colleague.email},
        cookies=owner_cookies,
    )

    as_owner = client.get(f"/workspaces/{workspace_id}", cookies=owner_cookies)
    as_member = client.get(f"/workspaces/{workspace_id}", cookies=member_cookies)

    assert as_owner.json()["role"] == "owner"
    assert as_member.json()["role"] == "member"
