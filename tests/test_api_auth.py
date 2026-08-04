"""Phase 13b: FastAPI auth router (signup + magic-link login + OAuth connect + /me).

Uses FastAPI's TestClient (real DB required — same requires_db convention as
the rest of the suite).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import ROLE_ADMIN, create_admin
from app.db.connection import get_connection

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("EMAIL_SENDER", "console")
    monkeypatch.setenv("FRONTEND_URL", "https://portal.example.com")
    monkeypatch.setenv("API_CORS_ORIGINS", "https://portal.example.com")


@pytest.fixture
def client():
    # Import after env vars are set so ApiSettings.from_env() picks them up.
    from app.api.main import create_app

    return TestClient(create_app())


@pytest.fixture
def _invited_member(store, org_cleanup):
    """(org_id, email) for a member an admin has already invited into a fresh org."""
    from app.auth import invite_member

    org_id = store.create_organization("API Auth Test Org")
    org_cleanup.append(org_id)
    email = f"invited-{uuid.uuid4().hex[:8]}@example.com"
    invite_member(email, org_id)
    return org_id, email


@requires_db
def test_signup_creates_pending_request_not_org_or_admin(client, signup_email_cleanup):
    email = f"founder-{uuid.uuid4().hex[:8]}@newco.example.com"
    signup_email_cleanup.append(email)

    response = client.post(
        "/auth/signup", json={"email": email, "company_name": f"NewCo {uuid.uuid4().hex[:8]}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert "pending" in body["message"].lower()
    assert "dev_link" not in body

    from app.auth import get_pending_request_for_email
    from app.auth.users import get_user_by_email

    assert get_user_by_email(email) is None  # no account/org created yet
    request = get_pending_request_for_email(email)
    assert request is not None
    assert request.status == "pending"


@requires_db
def test_signup_rejects_duplicate_pending_request(client, signup_email_cleanup):
    email = f"dup-{uuid.uuid4().hex[:8]}@newco.example.com"
    signup_email_cleanup.append(email)

    first = client.post("/auth/signup", json={"email": email, "company_name": "First Co"})
    assert first.status_code == 200

    second = client.post("/auth/signup", json={"email": email, "company_name": "Second Co"})
    assert second.status_code == 400


@requires_db
def test_signup_allowed_again_after_rejection(client, signup_email_cleanup):
    email = f"reapply-{uuid.uuid4().hex[:8]}@newco.example.com"
    signup_email_cleanup.append(email)

    first = client.post("/auth/signup", json={"email": email, "company_name": "First Co"})
    assert first.status_code == 200

    from app.auth import get_pending_request_for_email, reject_signup_request

    request = get_pending_request_for_email(email)
    reject_signup_request(request.id, reason="not a fit")

    second = client.post("/auth/signup", json={"email": email, "company_name": "Second Co"})
    assert second.status_code == 200


@requires_db
def test_signup_rejects_missing_fields(client):
    assert client.post("/auth/signup", json={"email": "not-an-email"}).status_code == 400
    assert client.post("/auth/signup", json={"company_name": "Acme"}).status_code == 400


@requires_db
def test_magic_link_request_for_invited_member_returns_generic_message(
    client, _invited_member
):
    org_id, email = _invited_member
    response = client.post("/auth/magic-link", json={"email": email})
    assert response.status_code == 200
    assert "sign-in link" in response.json()["message"].lower()


@requires_db
def test_magic_link_request_for_unknown_email_returns_same_generic_message_but_creates_nothing(
    client,
):
    email = "alice@never-invited.example"
    response = client.post("/auth/magic-link", json={"email": email})
    assert response.status_code == 200
    assert "sign-in link" in response.json()["message"].lower()

    from app.auth.users import get_user_by_email

    assert get_user_by_email(email) is None  # no account was ever created for it


@requires_db
def test_magic_link_request_rejects_malformed_email(client):
    response = client.post("/auth/magic-link", json={"email": "not-an-email"})
    assert response.status_code == 400


@requires_db
def test_admin_invite_then_magic_link_then_login_lands_in_admins_org(
    client, store, org_cleanup
):
    """The full flow this simplification exists for: an admin invites a
    specific email over HTTP, that email requests + consumes a magic link,
    and lands in exactly the inviting admin's org as a member."""
    from app.auth import create_session_token

    org_id = store.create_organization(f"API Auth Invite Flow Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    admin_cookies = {"session": create_session_token(admin)}

    invitee_email = f"teammate-{uuid.uuid4().hex[:8]}@example.com"
    invite_response = client.post(
        "/admin/members", json={"email": invitee_email}, cookies=admin_cookies
    )
    assert invite_response.status_code == 200

    login_response = client.post("/auth/magic-link", json={"email": invitee_email})
    assert login_response.status_code == 200

    from app.auth import create_magic_link_token

    token = create_magic_link_token(invitee_email)
    verify_response = client.get(
        f"/auth/magic-link/verify?token={token}", follow_redirects=False
    )
    assert verify_response.status_code in (302, 307)
    session_token = verify_response.cookies.get("session")
    assert session_token

    me_response = client.get("/me", cookies={"session": session_token})
    assert me_response.status_code == 200
    body = me_response.json()
    assert body["org_id"] == org_id
    assert body["role"] == "member"


@requires_db
def test_magic_link_full_login_flow_issues_session_scoped_to_correct_org(
    client, _invited_member
):
    # Passing the response's httpx.Cookies object straight through to the next
    # request silently drops it (a TestClient cookie-domain quirk); extracting
    # the value and passing a plain {"session": ...} dict works and still
    # exercises the real decode_session_token() path a browser cookie would.
    org_id, email = _invited_member

    client.post("/auth/magic-link", json={"email": email})

    # Extract the raw token the same way the console sender "delivered" it:
    # request the token directly via the auth module (equivalent to reading it
    # off the printed console link in dev).
    from app.auth import create_magic_link_token

    token = create_magic_link_token(email)
    verify_response = client.get(
        f"/auth/magic-link/verify?token={token}", follow_redirects=False
    )
    assert verify_response.status_code in (302, 307)
    session_token = verify_response.cookies.get("session")
    assert session_token

    me_response = client.get("/me", cookies={"session": session_token})
    assert me_response.status_code == 200
    body = me_response.json()
    assert body["org_id"] == org_id
    assert body["role"] == "member"


@requires_db
def test_magic_link_verify_rejects_reused_token(client, _invited_member):
    org_id, email = _invited_member
    client.post("/auth/magic-link", json={"email": email})

    from app.auth import create_magic_link_token

    token = create_magic_link_token(email)
    first = client.get(f"/auth/magic-link/verify?token={token}", follow_redirects=False)
    assert first.status_code in (302, 307)

    second = client.get(f"/auth/magic-link/verify?token={token}", follow_redirects=False)
    assert second.status_code == 401


@requires_db
def test_magic_link_verify_rejects_unknown_token(client):
    response = client.get("/auth/magic-link/verify?token=not-a-real-token")
    assert response.status_code == 401


@requires_db
def test_me_requires_a_session(client):
    response = client.get("/me")
    assert response.status_code == 401


@requires_db
def test_oauth_authorize_requires_admin_role(client, store, org_cleanup):
    org_id = store.create_organization("API Auth Non-Admin Org")
    org_cleanup.append(org_id)
    member = create_admin(f"nonadmin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    # Force member role for this check (create_admin always makes an admin —
    # flip it directly to prove a non-admin session is rejected).
    with get_connection() as conn:
        conn.execute("UPDATE users SET role = 'member' WHERE id = %s", (member.id,))

    from app.auth import create_session_token
    from app.auth.users import get_user

    session_token = create_session_token(get_user(member.id))

    response = client.get(
        "/auth/notion/authorize",
        cookies={"session": session_token},
        follow_redirects=False,
    )
    assert response.status_code == 403


@requires_db
def test_oauth_authorize_redirects_admin_to_provider(
    client, store, org_cleanup, monkeypatch
):
    org_id = store.create_organization("API Auth Admin Org")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)

    monkeypatch.setenv("NOTION_CLIENT_ID", "client-123")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("NOTION_REDIRECT_URI", "https://portal.example.com/auth/notion/callback")

    from app.auth import create_session_token

    session_token = create_session_token(admin)
    response = client.get(
        "/auth/notion/authorize",
        cookies={"session": session_token},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("https://api.notion.com/v1/oauth/authorize")
    assert f"org_id" not in response.headers["location"]  # org never leaks into the URL


@requires_db
def test_oauth_callback_rejects_unknown_state(client, monkeypatch):
    monkeypatch.setenv("NOTION_CLIENT_ID", "client-123")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("NOTION_REDIRECT_URI", "https://portal.example.com/auth/notion/callback")

    response = client.get("/auth/notion/callback?code=abc&state=not-a-real-state")
    assert response.status_code == 400


# --- Workspace-within-a-Workspace: workspace-scoped connect flow (Task 7) ---


@requires_db
def test_workspace_authorize_requires_owner_role(client, store, org_cleanup, monkeypatch):
    from app.auth import create_session_token
    from app.workspaces import create_workspace, invite_member

    monkeypatch.setenv("NOTION_CLIENT_ID", "client-123")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("NOTION_REDIRECT_URI", "https://portal.example.com/auth/notion/callback")

    org_id = store.create_organization("Workspace OAuth Org")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    from app.auth.users import invite_member as invite_org_member

    colleague = invite_org_member(f"colleague-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)
    invite_member(workspace_id, org_id, owner.id, colleague.email)

    from app.auth.users import get_user

    member_session = create_session_token(get_user(colleague.id))
    response = client.get(
        f"/auth/notion/authorize?workspace_id={workspace_id}",
        cookies={"session": member_session},
        follow_redirects=False,
    )
    assert response.status_code == 403


@requires_db
def test_workspace_authorize_rejects_non_member(client, store, org_cleanup, monkeypatch):
    from app.auth import create_session_token
    from app.workspaces import create_workspace

    monkeypatch.setenv("NOTION_CLIENT_ID", "client-123")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("NOTION_REDIRECT_URI", "https://portal.example.com/auth/notion/callback")

    org_id = store.create_organization("Workspace OAuth Non-Member Org")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    stranger = create_admin(f"stranger-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)

    session_token = create_session_token(stranger)
    response = client.get(
        f"/auth/notion/authorize?workspace_id={workspace_id}",
        cookies={"session": session_token},
        follow_redirects=False,
    )
    assert response.status_code == 403


@requires_db
def test_workspace_owner_authorize_redirects_to_provider(
    client, store, org_cleanup, monkeypatch
):
    from app.auth import create_session_token
    from app.workspaces import create_workspace

    monkeypatch.setenv("NOTION_CLIENT_ID", "client-123")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("NOTION_REDIRECT_URI", "https://portal.example.com/auth/notion/callback")

    org_id = store.create_organization("Workspace OAuth Owner Org")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)

    session_token = create_session_token(owner)
    response = client.get(
        f"/auth/notion/authorize?workspace_id={workspace_id}",
        cookies={"session": session_token},
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("https://api.notion.com/v1/oauth/authorize")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT workspace_id::text FROM oauth_states WHERE org_id = %s ORDER BY created_at DESC LIMIT 1",
            (org_id,),
        ).fetchone()
    assert row[0] == workspace_id


@requires_db
def test_workspace_oauth_callback_saves_connection_scoped_to_workspace(
    client, store, org_cleanup, monkeypatch
):
    """Full authorize -> callback roundtrip for a workspace connect flow."""
    from app.auth import create_session_token
    from app.auth.oauth_state import create_state
    from app.workspaces import create_workspace

    monkeypatch.setenv("NOTION_CLIENT_ID", "client-123")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "secret-456")
    monkeypatch.setenv("NOTION_REDIRECT_URI", "https://portal.example.com/auth/notion/callback")
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())

    org_id = store.create_organization("Workspace OAuth Callback Org")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)

    # Simulate the callback by mocking the token exchange (no real Notion call).
    from app.auth.notion_oauth import NotionOAuthProvider
    from app.auth.base import OAuthTokens

    def _fake_exchange(self, code):
        return OAuthTokens(
            access_token="ntn_workspace_secret",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-workspace-connect",
            external_workspace_name="Employee's Notion",
        )

    monkeypatch.setattr(NotionOAuthProvider, "exchange_code", _fake_exchange)

    state = create_state(org_id, "notion", workspace_id=workspace_id)
    response = client.get(
        f"/auth/notion/callback?code=abc&state={state}", follow_redirects=False
    )
    assert response.status_code in (302, 307)
    assert f"/workspaces/{workspace_id}" in response.headers["location"]

    with get_connection() as conn:
        row = conn.execute(
            "SELECT workspace_id::text, external_workspace_id FROM oauth_connections "
            "WHERE org_id = %s AND provider = 'notion' AND workspace_id = %s",
            (org_id, workspace_id),
        ).fetchone()
    assert row is not None
    assert row[0] == workspace_id
    assert row[1] == "ws-workspace-connect"

    # The org-wide (workspace_id IS NULL) slot must be untouched by this connect.
    with get_connection() as conn:
        org_wide = conn.execute(
            "SELECT 1 FROM oauth_connections "
            "WHERE org_id = %s AND provider = 'notion' AND workspace_id IS NULL",
            (org_id,),
        ).fetchone()
    assert org_wide is None
