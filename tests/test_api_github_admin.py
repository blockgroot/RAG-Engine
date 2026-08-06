"""Admin routes against a GitHub connection (Plan Phase 7).

GitHub is connected like Notion/Drive but behaves nothing like them once
connected: it has **no ingestion at all** (revision 1 — nothing is embedded), so
the sync-shaped endpoints must refuse it clearly rather than half-work.

The one that actually matters is ``/ingest``: without a guard it would happily
enqueue a job the worker cannot run, so an admin would see a queued job silently
fail later with an obscure "Unknown source type" error. A 400 up front says the
true thing — there is nothing to sync, because answers are read live.
"""

from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth import OAuthTokens, create_admin, create_session_token, save_connection
from app.auth import set_connection_config

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("EMAIL_SENDER", "console")


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


@pytest.fixture
def github_org(store, org_cleanup):
    """(org_id, cookies, connection_id) for an org with GitHub connected."""
    org_id = store.create_organization(f"GH Admin Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    connection_id = save_connection(
        org_id,
        "github",
        OAuthTokens(
            access_token="ghu_user",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="acme-inc",
            external_workspace_name="acme-inc (Organization)",
        ),
    )
    set_connection_config(
        org_id,
        "github",
        {
            "installation_id": "4242",
            "account_login": "acme-inc",
            "repository_selection": "selected",
            "repos": [
                {
                    "full_name": "acme-inc/handbook",
                    "description": "Engineering handbook",
                    "topics": ["docs"],
                }
            ],
        },
    )
    return org_id, {"session": create_session_token(admin)}, connection_id


@requires_db
def test_ingest_is_refused_for_github(client, github_org):
    """The important guard: never enqueue a job the worker cannot run."""
    _, cookies, connection_id = github_org

    response = client.post(
        f"/admin/connections/{connection_id}/ingest", cookies=cookies
    )

    assert response.status_code == 400
    assert "live" in response.json()["detail"].lower()


@requires_db
def test_changes_is_refused_for_github(client, github_org):
    _, cookies, connection_id = github_org

    response = client.get(
        f"/admin/connections/{connection_id}/changes", cookies=cookies
    )

    assert response.status_code == 400


@requires_db
def test_folder_config_is_refused_for_github(client, github_org):
    """Repo scope comes from GitHub's install screen, not a config form."""
    _, cookies, connection_id = github_org

    response = client.put(
        f"/admin/connections/{connection_id}/config",
        json={"folder_url": "https://drive.google.com/drive/folders/xyz"},
        cookies=cookies,
    )

    assert response.status_code == 400


@requires_db
def test_connections_listing_exposes_the_authorized_repo_scope(client, github_org):
    """The Sources UI shows what was actually authorized, not an assumption."""
    _, cookies, connection_id = github_org

    response = client.get("/admin/connections", cookies=cookies)

    assert response.status_code == 200
    github = next(c for c in response.json() if c["provider"] == "github")
    config = github["source_config"]
    assert config["repository_selection"] == "selected"
    assert config["repos"][0]["full_name"] == "acme-inc/handbook"


@requires_db
def test_refresh_scope_rereads_the_authorized_repos(client, github_org, monkeypatch):
    org_id, cookies, connection_id = github_org

    monkeypatch.setattr(
        "app.auth.credentials._github_installation_token",
        lambda org_id, workspace_id=None: "ghs_token",
    )

    class _Repo:
        def __init__(self, full_name):
            self.full_name = full_name
            self.description = None
            self.topics = ()

    monkeypatch.setattr(
        "app.githublive.scope.fetch_installation_repos",
        lambda token, **kw: ("all", [_Repo("acme-inc/handbook"), _Repo("acme-inc/new-svc")]),
    )

    response = client.post(
        f"/admin/connections/{connection_id}/refresh-scope", cookies=cookies
    )

    assert response.status_code == 200
    body = response.json()
    assert body["repository_selection"] == "all"
    assert body["repo_count"] == 2

    # And it was persisted, not just returned.
    from app.githublive import load_scope

    assert load_scope(org_id).repository_selection == "all"


@requires_db
def test_refresh_scope_is_refused_for_a_non_github_connection(
    client, store, org_cleanup
):
    org_id = store.create_organization(f"Notion Only Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_x",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-1",
        ),
    )
    cookies = {"session": create_session_token(admin)}

    response = client.post(
        f"/admin/connections/{connection_id}/refresh-scope", cookies=cookies
    )

    assert response.status_code == 400


@requires_db
def test_refresh_scope_never_touches_another_orgs_connection(
    client, github_org, store, org_cleanup
):
    """Same org-scoping guarantee every admin route has."""
    _, _, victim_connection_id = github_org

    other_org = store.create_organization(f"Other Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other_org)
    other_admin = create_admin(f"other-{uuid.uuid4().hex[:8]}@example.com", other_org)
    cookies = {"session": create_session_token(other_admin)}

    response = client.post(
        f"/admin/connections/{victim_connection_id}/refresh-scope", cookies=cookies
    )

    assert response.status_code == 404


@requires_db
def test_a_workspace_owner_may_connect_github(client, store, org_cleanup, monkeypatch):
    """REVERSED, deliberately and on request.

    This test previously asserted a 400: workspace-scoped GitHub was an explicit
    non-goal, because a per-workspace repo subset introduces repo-level access
    control inside an org — a dimension nothing else in this system has. That
    decision was overturned, so the assertion is inverted rather than deleted, to
    keep the reversal visible in the history.

    What makes it safe is enforced elsewhere and tested in
    ``tests/test_github_workspace_scope.py``: a workspace's GitHub answers come
    from that workspace's OWN installation, and a workspace with no GitHub
    connection never falls back to the org-wide one.
    """
    from app.workspaces import create_workspace

    monkeypatch.setenv("GITHUB_APP_SLUG", "acme-rag")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.abc123")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "s3cret")

    org_id = store.create_organization(f"GH WS Connect Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Personal Notes", owner.id)
    cookies = {"session": create_session_token(owner)}

    response = client.get(
        f"/auth/github/authorize?workspace_id={workspace_id}",
        cookies=cookies,
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert "installations/new" in response.headers["location"]


@requires_db
def test_me_reports_github_connected_for_members_too(client, github_org, store):
    """Members must see the Code tab; they just can't manage the connection."""
    from app.auth import invite_member

    org_id, admin_cookies, _ = github_org
    member = invite_member(f"member-{uuid.uuid4().hex[:8]}@example.com", org_id)
    member_cookies = {"session": create_session_token(member)}

    response = client.get("/me", cookies=member_cookies)

    assert response.status_code == 200
    assert response.json()["github_connected"] is True


@requires_db
def test_me_reports_no_github_when_unconnected(client, store, org_cleanup):
    org_id = store.create_organization(f"No GH Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)

    response = client.get("/me", cookies={"session": create_session_token(admin)})

    assert response.json()["github_connected"] is False
