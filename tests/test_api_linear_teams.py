"""The Linear team picker: company Sources and a space's own connection.

Linear's sign-in grants every team the account can see, so the teams are
picked after connecting, and a dropped team's issues stop answering at once.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import OAuthTokens, create_admin, create_session_token, save_connection
from app.auth.credentials import get_connection_config

from .conftest import requires_db

TEAMS = [{"id": "T1", "key": "ENG", "name": "Engineering", "visibility": "public"},
         {"id": "T2", "key": "SYV", "name": "Syvora", "visibility": "private"}]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())

    class Teams:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"teams": {"nodes": TEAMS,
                                       "pageInfo": {"hasNextPage": False, "endCursor": None}}}}

    monkeypatch.setattr("app.sources.linear.httpx.post", lambda url, json, headers, timeout: Teams())


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


@pytest.fixture
def org(store, org_cleanup):
    org_id = store.create_organization(f"Linear Teams {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"lt-{uuid.uuid4().hex[:8]}@example.com", org_id)
    return org_id, admin, {"session": create_session_token(admin)}


@pytest.fixture
def side_effects(monkeypatch):
    calls = {"synced": [], "purged": []}
    monkeypatch.setattr("app.jobs.autosync.sync_now",
                        lambda org, conn, provider, workspace_id=None: calls["synced"].append(workspace_id))
    monkeypatch.setattr("app.api.connection_ops.purge_provider_documents",
                        lambda org, provider, workspace_id=None: calls["purged"].append(workspace_id) or 4)
    return calls


def _linear(org_id, workspace_id=None):
    return save_connection(org_id, "linear", OAuthTokens("lin", None, None, "lin-ws", "Syvora"),
                           workspace_id=workspace_id)


@requires_db
def test_company_linear_lists_teams_and_syncs_only_the_picked_ones(client, org, side_effects):
    org_id, _admin, cookies = org
    conn = _linear(org_id)
    teams = client.get(f"/admin/connections/{conn}/linear-teams", cookies=cookies).json()["teams"]
    assert [(t["key"], t["private"]) for t in teams] == [("ENG", False), ("SYV", True)]

    # The first pick purges: a connection from before the picker read every team.
    saved = client.put(f"/admin/connections/{conn}/config", json={"team_ids": ["T1", "T2"]}, cookies=cookies)
    assert saved.status_code == 200 and saved.json()["teams_changed"] is True
    assert side_effects == {"synced": [None], "purged": [None]}

    # Adding a team keeps what is there.
    again = client.put(f"/admin/connections/{conn}/config", json={"team_ids": ["T1", "T2"]}, cookies=cookies)
    assert again.json()["teams_changed"] is False and side_effects["purged"] == [None]

    # Dropping a team takes its issues out before the next sync.
    dropped = client.put(f"/admin/connections/{conn}/config", json={"team_ids": ["T2"]}, cookies=cookies)
    assert dropped.json()["teams_changed"] is True and dropped.json()["documents_purged"] == 4
    assert get_connection_config(org_id, "linear")["team_names"] == {"T2": "Syvora"}

    unknown = client.put(f"/admin/connections/{conn}/config", json={"team_ids": ["T9"]}, cookies=cookies)
    assert unknown.status_code == 400


@requires_db
def test_a_spaces_linear_picks_its_own_teams(client, org, side_effects):
    from app.workspaces.store import create_workspace

    org_id, admin, cookies = org
    space = create_workspace(org_id, "Design", admin.id)
    conn = _linear(org_id, workspace_id=space)
    assert client.get(f"/workspaces/{space}/connections/{conn}/linear-teams",
                      cookies=cookies).status_code == 200
    saved = client.put(f"/workspaces/{space}/connections/{conn}/config",
                       json={"team_ids": ["T1"]}, cookies=cookies)
    assert saved.status_code == 200
    assert get_connection_config(org_id, "linear", workspace_id=space)["team_ids"] == ["T1"]
    assert get_connection_config(org_id, "linear") is None          # the company's is untouched
    assert side_effects["synced"] == [space]
