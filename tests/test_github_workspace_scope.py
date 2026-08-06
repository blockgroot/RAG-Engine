"""Workspace-scoped GitHub connections.

**This reverses an earlier explicit non-goal, deliberately and on request.** The
original GitHub plan argued that a per-workspace repo subset introduces
repo-level access control inside an org — a dimension nothing else in this system
has — and refused it server-side. That refusal is lifted: a workspace owner may
connect their own GitHub installation, which makes **workspace membership a real
access boundary over code**, not just over documents.

That makes one property load-bearing above all others, and it is what most of
this file tests: a workspace's GitHub answers must come from **that workspace's
own** installation, and a workspace with no GitHub connection must **never**
silently fall back to the org-wide one. A fallback would mean inviting a
colleague into a meeting-notes workspace quietly handed them the whole company's
code.
"""

from __future__ import annotations

import json
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.auth import (
    OAuthTokens,
    create_admin,
    create_session_token,
    save_connection,
    set_connection_config,
)
from app.core.exceptions import ConfigurationError, SourceError
from app.githublive import load_scope, resolve_repo
from app.workspaces import create_workspace

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("EMAIL_SENDER", "console")
    monkeypatch.setenv("GITHUB_APP_SLUG", "acme-rag")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "Iv1.abc123")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "s3cret")


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


def _connect_github(org_id, account, repos, *, workspace_id=None) -> str:
    connection_id = save_connection(
        org_id,
        "github",
        OAuthTokens(
            access_token=f"ghu_{account}",
            refresh_token=None,
            expires_at=None,
            external_workspace_id=account,
        ),
        workspace_id=workspace_id,
    )
    set_connection_config(
        org_id,
        "github",
        {
            "installation_id": f"inst-{account}",
            "account_login": account,
            "repository_selection": "selected",
            "repos": [{"full_name": r, "description": None, "topics": []} for r in repos],
        },
        workspace_id=workspace_id,
    )
    return connection_id


# -- the isolation properties that make this safe --------------------------


@requires_db
def test_workspace_github_scope_is_independent_of_the_org_wide_one(store, org_cleanup):
    org_id = store.create_organization(f"GH WS Scope {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Side Project", owner.id)

    _connect_github(org_id, "acme-inc", ["acme-inc/payroll", "acme-inc/handbook"])
    _connect_github(
        org_id, "sana-personal", ["sana-personal/notes"], workspace_id=workspace_id
    )

    org_scope = load_scope(org_id)
    ws_scope = load_scope(org_id, workspace_id)

    assert {r.full_name for r in org_scope.repos} == {
        "acme-inc/payroll",
        "acme-inc/handbook",
    }
    assert {r.full_name for r in ws_scope.repos} == {"sana-personal/notes"}
    # The workspace cannot reach the org's repos ...
    with pytest.raises(SourceError):
        resolve_repo(ws_scope, "acme-inc/payroll")
    # ... and the org-wide scope cannot reach the workspace's.
    with pytest.raises(SourceError):
        resolve_repo(org_scope, "sana-personal/notes")


@requires_db
def test_a_workspace_without_github_never_falls_back_to_the_org_connection(
    store, org_cleanup
):
    """The single most important property of this feature.

    If this ever starts returning the org-wide scope, workspace membership stops
    being an access boundary and this whole feature becomes a data leak.
    """
    org_id = store.create_organization(f"GH WS NoFallback {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Notes Only", owner.id)

    _connect_github(org_id, "acme-inc", ["acme-inc/payroll"])  # org-wide ONLY

    with pytest.raises(ConfigurationError):
        load_scope(org_id, workspace_id)


@requires_db
def test_sibling_workspaces_cannot_see_each_others_repos(store, org_cleanup):
    org_id = store.create_organization(f"GH WS Siblings {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    ws_a = create_workspace(org_id, "Alpha", owner.id)
    ws_b = create_workspace(org_id, "Beta", owner.id)

    _connect_github(org_id, "alpha-acct", ["alpha-acct/alpha"], workspace_id=ws_a)
    _connect_github(org_id, "beta-acct", ["beta-acct/beta"], workspace_id=ws_b)

    scope_a = load_scope(org_id, ws_a)

    assert {r.full_name for r in scope_a.repos} == {"alpha-acct/alpha"}
    with pytest.raises(SourceError):
        resolve_repo(scope_a, "beta-acct/beta")


# -- connect flow ----------------------------------------------------------


@requires_db
def test_a_workspace_owner_may_now_start_a_github_connect(client, store, org_cleanup):
    """Previously refused with 400; the non-goal is lifted."""
    org_id = store.create_organization(f"GH WS Connect {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Side Project", owner.id)

    response = client.get(
        f"/auth/github/authorize?workspace_id={workspace_id}",
        cookies={"session": create_session_token(owner)},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307)
    assert "github.com/apps/acme-rag/installations/new" in response.headers["location"]


@requires_db
def test_a_non_owner_member_still_cannot_connect_github_to_a_workspace(
    client, store, org_cleanup
):
    """Only the owner may repoint a workspace's data source — unchanged rule.

    This matters more now than it did for documents: an ordinary member who could
    connect GitHub could widen what the whole workspace reads.
    """
    from app.auth import invite_member as invite_org_member
    from app.workspaces import invite_member as invite_workspace_member

    org_id = store.create_organization(f"GH WS NonOwner {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Side Project", owner.id)

    member = invite_org_member(f"m-{uuid.uuid4().hex[:8]}@example.com", org_id)
    invite_workspace_member(workspace_id, org_id, owner.id, member.email)

    response = client.get(
        f"/auth/github/authorize?workspace_id={workspace_id}",
        cookies={"session": create_session_token(member)},
        follow_redirects=False,
    )

    assert response.status_code == 403


# -- workspace API surface -------------------------------------------------


@requires_db
def test_workspace_detail_reports_its_own_github_connection_only(
    client, store, org_cleanup
):
    """An org-wide GitHub connection must NOT light up a workspace's Code tab."""
    org_id = store.create_organization(f"GH WS Detail {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Notes", owner.id)
    cookies = {"session": create_session_token(owner)}

    _connect_github(org_id, "acme-inc", ["acme-inc/payroll"])  # org-wide only

    detail = client.get(f"/workspaces/{workspace_id}", cookies=cookies).json()
    assert detail["github_connected"] is False

    _connect_github(org_id, "sana", ["sana/notes"], workspace_id=workspace_id)

    detail = client.get(f"/workspaces/{workspace_id}", cookies=cookies).json()
    assert detail["github_connected"] is True


@requires_db
def test_workspace_sync_routes_refuse_a_github_connection(client, store, org_cleanup):
    """No ingestion for GitHub in a workspace either — same guard as admin."""
    org_id = store.create_organization(f"GH WS Guards {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Side Project", owner.id)
    cookies = {"session": create_session_token(owner)}
    connection_id = _connect_github(
        org_id, "sana", ["sana/notes"], workspace_id=workspace_id
    )

    ingest = client.post(
        f"/workspaces/{workspace_id}/connections/{connection_id}/ingest", cookies=cookies
    )
    changes = client.get(
        f"/workspaces/{workspace_id}/connections/{connection_id}/changes", cookies=cookies
    )

    assert ingest.status_code == 400
    assert changes.status_code == 400


@requires_db
def test_workspace_refresh_scope_is_owner_only_and_workspace_scoped(
    client, store, org_cleanup, monkeypatch
):
    org_id = store.create_organization(f"GH WS Refresh {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Side Project", owner.id)
    connection_id = _connect_github(
        org_id, "sana", ["sana/notes"], workspace_id=workspace_id
    )

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
        lambda token, **kw: ("selected", [_Repo("sana/notes"), _Repo("sana/new-thing")]),
    )

    response = client.post(
        f"/workspaces/{workspace_id}/connections/{connection_id}/refresh-scope",
        cookies={"session": create_session_token(owner)},
    )

    assert response.status_code == 200
    assert response.json()["repo_count"] == 2
    # Persisted to the WORKSPACE row, and the org-wide scope is untouched.
    assert len(load_scope(org_id, workspace_id).repos) == 2
    with pytest.raises(ConfigurationError):
        load_scope(org_id)


@requires_db
def test_workspace_code_suggestions_come_from_the_workspace_installation(
    client, store, org_cleanup
):
    """Chips must not advertise repos the workspace cannot actually read."""
    org_id = store.create_organization(f"GH WS Chips {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Side Project", owner.id)
    cookies = {"session": create_session_token(owner)}

    _connect_github(org_id, "acme-inc", ["acme-inc/payroll"])
    _connect_github(org_id, "sana", ["sana/notes"], workspace_id=workspace_id)

    body = client.get(
        f"/chat/suggestions?agent=github&workspace_id={workspace_id}", cookies=cookies
    ).json()

    rendered = " ".join(body["questions"])
    assert "notes" in rendered
    assert "payroll" not in rendered, "must never surface the org-wide installation"


@requires_db
def test_github_conversations_are_refused_in_a_workspace_too(
    client, store, org_cleanup
):
    """The missing capability is the agent's (no memory), not the scope's."""
    org_id = store.create_organization(f"GH WS Conv {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Side Project", owner.id)

    response = client.post(
        "/chat/conversations",
        json={"agent": "github", "workspace_id": workspace_id},
        cookies={"session": create_session_token(owner)},
    )

    assert response.status_code == 400


# -- routing / agent scoping ----------------------------------------------


def test_workspace_github_answer_is_scoped_to_that_workspace():
    """The ``workspace_id`` must reach the reader builder unchanged.

    This is the unit-level counterpart to the no-fallback DB test: routing is
    only safe because the agent builds a workspace-scoped reader.
    """
    from app.agent.github_agent import GitHubAgent
    from app.config.settings import GitHubAgentSettings
    from app.githublive import RepoRef
    from app.githublive.base import RepoReadme
    from app.llm.base import ChatResult, ToolCall

    seen: dict = {}

    class _Reader:
        def list_repos(self):
            return [RepoRef("sana/notes", "My notes", ())]

        def get_readme(self, repo):
            return RepoReadme(
                repo="sana/notes",
                content="# notes\n\nMeeting notes.",
                url="https://github.com/sana/notes#readme",
            )

        def get_commit(self, repo, sha):  # pragma: no cover
            raise AssertionError("not expected")

        def list_commits(self, repo, **kwargs):  # pragma: no cover
            return []

    class _LLM:
        def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
            return ChatResult(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="c", name="get_readme", arguments=json.dumps({"repo": "notes"})
                    )
                ],
            )

        def generate(self, prompt, **kwargs):
            return "MODE: A\n\nPersonal meeting notes."

    def _build(org_id, workspace_id=None):
        seen["org_id"] = org_id
        seen["workspace_id"] = workspace_id
        return _Reader()

    agent = GitHubAgent(
        llm=_LLM(),
        reader_builder=_build,
        fallback_response="nope",
        settings=GitHubAgentSettings(),
    )

    response = agent.answer("what is this?", "org-1", workspace_id="ws-9")

    assert seen == {"org_id": "org-1", "workspace_id": "ws-9"}
    assert response.source == "github"
    assert response.grounded is True
