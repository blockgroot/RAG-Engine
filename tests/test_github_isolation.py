"""Cross-tenant isolation for the GitHub path (Plan Phase 8).

``tests/test_isolation.py`` proves the *retrieval* boundary: a query for one org
can never see another's chunks, because every read carries ``WHERE org_id``.
GitHub needs its own proof because it does not use retrieval at all — nothing is
embedded, so there are no chunks to scope. Its boundary is made of three
different things, and each gets a test here:

1. **Stored scope is per-connection.** ``load_scope(org)`` reads that org's own
   ``oauth_connections`` row, so one tenant can never learn another's repo list.
2. **``resolve_repo`` is the enforcement point.** Even holding a valid reader,
   naming another tenant's repo is refused before any request is issued. This is
   the live path's equivalent of the ``WHERE org_id`` clause.
3. **A workspace connection is not an org connection.** A personal
   sub-workspace's row (``workspace_id IS NOT NULL``) must not satisfy an
   org-wide lookup, or a workspace owner could quietly repoint org-wide answers.
"""

from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet

from app.auth import OAuthTokens, save_connection, set_connection_config
from app.core.exceptions import ConfigurationError, SourceError
from app.githublive import load_scope, resolve_repo

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


def _connect(org_id: str, account: str, repos: list[str], *, workspace_id=None) -> None:
    save_connection(
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


@requires_db
def test_each_org_sees_only_its_own_authorized_repos(store, org_cleanup):
    org_a = store.create_organization(f"GH Iso A {uuid.uuid4().hex[:8]}")
    org_b = store.create_organization(f"GH Iso B {uuid.uuid4().hex[:8]}")
    org_cleanup.extend([org_a, org_b])

    _connect(org_a, "acme-inc", ["acme-inc/handbook", "acme-inc/payments-svc"])
    _connect(org_b, "globex", ["globex/secret-payroll"])

    scope_a = load_scope(org_a)
    scope_b = load_scope(org_b)

    assert {r.full_name for r in scope_a.repos} == {
        "acme-inc/handbook",
        "acme-inc/payments-svc",
    }
    assert {r.full_name for r in scope_b.repos} == {"globex/secret-payroll"}
    # The decisive assertion: neither tenant's scope leaks into the other.
    assert scope_a.installation_id != scope_b.installation_id
    assert "globex/secret-payroll" not in {r.full_name for r in scope_a.repos}


@requires_db
def test_one_org_cannot_read_another_orgs_repo_even_by_naming_it(store, org_cleanup):
    """The live path's ``WHERE org_id`` equivalent.

    An LLM that hallucinates (or is coaxed into emitting) a competitor's repo
    name must be stopped by scope, not by luck.
    """
    org_a = store.create_organization(f"GH Iso Naming A {uuid.uuid4().hex[:8]}")
    org_b = store.create_organization(f"GH Iso Naming B {uuid.uuid4().hex[:8]}")
    org_cleanup.extend([org_a, org_b])

    _connect(org_a, "acme-inc", ["acme-inc/handbook"])
    _connect(org_b, "globex", ["globex/secret-payroll"])

    scope_a = load_scope(org_a)

    with pytest.raises(SourceError):
        resolve_repo(scope_a, "globex/secret-payroll")
    # And its own repo still resolves, so this isn't a blanket refusal.
    assert resolve_repo(scope_a, "acme-inc/handbook") == "acme-inc/handbook"


@requires_db
def test_all_repositories_selection_still_refuses_a_foreign_owner(store, org_cleanup):
    """"All repositories" means all of THIS account's — not all of GitHub's."""
    org_id = store.create_organization(f"GH Iso All {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    save_connection(
        org_id,
        "github",
        OAuthTokens(
            access_token="ghu_a",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="acme-inc",
        ),
    )
    set_connection_config(
        org_id,
        "github",
        {
            "installation_id": "inst-1",
            "account_login": "acme-inc",
            "repository_selection": "all",
            "repos": [],
        },
    )

    scope = load_scope(org_id)

    assert resolve_repo(scope, "acme-inc/anything") == "acme-inc/anything"
    with pytest.raises(SourceError):
        resolve_repo(scope, "globex/secret-payroll")


@requires_db
def test_a_workspace_connection_does_not_satisfy_an_org_wide_lookup(
    store, org_cleanup
):
    """Otherwise a workspace owner could repoint org-wide GitHub answers."""
    from app.workspaces import create_workspace
    from app.auth import create_admin

    org_id = store.create_organization(f"GH Iso WS {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Personal", owner.id)

    # Only a workspace-scoped connection exists; none org-wide.
    _connect(org_id, "sneaky", ["sneaky/repo"], workspace_id=workspace_id)

    with pytest.raises(ConfigurationError):
        load_scope(org_id)  # org-wide lookup must find nothing


@requires_db
def test_scope_lookup_for_an_unconnected_org_raises_rather_than_returning_empty(
    store, org_cleanup
):
    """"Not connected" and "connected with nothing authorized" are different.

    Collapsing them would make a missing connection look like an empty
    allowlist, hiding a misconfiguration behind a generic fallback.
    """
    org_id = store.create_organization(f"GH Iso None {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    with pytest.raises(ConfigurationError):
        load_scope(org_id)
