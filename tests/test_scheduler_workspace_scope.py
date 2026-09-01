"""Space-scoped schedulers read the SPACE's connection, never the org's.

What is left here is **GitHub**, the only provider whose report still resolves a
credential to read activity. It saves BOTH an org-wide and a workspace
connection for the same provider with *different* scopes, so a fetcher reading
the wrong one is visible rather than inferred — "it is threaded through" being
exactly the claim that is true in three places and quietly false in the fourth.

Slack, Linear, Notion and Drive moved to reading our own index
(``activity.py::fetch_indexed_activity``), so their scope is enforced by a
WHERE clause instead of by credential resolution — a different and sharper
risk, since a missing predicate leaks another space's documents rather than
merely failing. That is proved in BOTH directions in
``tests/test_scheduler_indexed.py``, along with the rule this file still
covers: a space without the connection raises instead of falling back.

Real Postgres, no network: the reader factory is stubbed to record what it was
handed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.auth import OAuthTokens, save_connection
from app.auth.credentials import set_connection_config
from app.auth.users import invite_member
from app.schedulers import activity
from app.workspaces.store import create_workspace

from .conftest import requires_db

SINCE = datetime.now(timezone.utc) - timedelta(days=7)

ORG_TOKEN = "org-wide-token"
SPACE_TOKEN = "space-only-token"


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


def _tokens(access_token: str) -> OAuthTokens:
    return OAuthTokens(
        access_token=access_token,
        refresh_token=None,
        expires_at=None,
        external_workspace_id=f"ext-{uuid.uuid4().hex[:6]}",
    )


@pytest.fixture
def org_and_space(store, org_cleanup):
    """An org with a provider connected BOTH org-wide and inside one space."""
    org_id = store.create_organization(f"Sched Scope Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = invite_member(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting notes", owner.id)
    return org_id, owner.id, workspace_id




# --------------------------------------------------------------------------
# Slack
# --------------------------------------------------------------------------






# --------------------------------------------------------------------------
# Linear
# --------------------------------------------------------------------------




def _connect_both(org_id: str, workspace_id: str, provider: str) -> None:
    """Same provider connected org-wide AND in the space, with DIFFERENT
    tokens — so a fetcher reading the wrong scope is visible, not inferred."""
    save_connection(org_id, provider, _tokens(ORG_TOKEN))
    save_connection(org_id, provider, _tokens(SPACE_TOKEN), workspace_id=workspace_id)


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------


@requires_db
def test_github_in_a_space_reads_that_spaces_installation(org_and_space, monkeypatch):
    """GitHub scope lives in source_config, not a token — so this asserts the
    *repo list* came from the space's connection. A space must never be handed
    the company installation (CLAUDE.md §5)."""
    org_id, _, workspace_id = org_and_space
    _connect_both(org_id, workspace_id, "github")
    set_connection_config(
        org_id,
        "github",
        {
            "installation_id": "111",
            "repository_selection": "selected",
            "repos": [{"full_name": "acme/company-api"}],
        },
    )
    set_connection_config(
        org_id,
        "github",
        {
            "installation_id": "222",
            "repository_selection": "selected",
            "repos": [{"full_name": "acme/space-notes"}],
        },
        workspace_id=workspace_id,
    )

    asked: list[str] = []

    class _Reader:
        def list_commits(self, repo, *, since=None, limit=10):
            asked.append(repo)
            return []

    monkeypatch.setattr(
        "app.githublive.build_github_reader", lambda *a, **k: _Reader()
    )

    digest = activity.fetch_github_activity(
        org_id, SINCE, workspace_id=workspace_id
    )

    assert asked == ["acme/space-notes"]
    assert "acme/company-api" not in " ".join(digest.notes)


@requires_db
def test_a_space_without_the_provider_raises_instead_of_falling_back(
    org_and_space, monkeypatch
):
    """Slack connected org-wide but NOT in the space: the fetch must fail, not
    quietly report the company's channels to a space member."""
    org_id, _, workspace_id = org_and_space
    save_connection(org_id, "slack", _tokens(ORG_TOKEN))
    set_connection_config(org_id, "slack", {"channel_ids": ["C-ORG"]})

    from app.core.exceptions import ProviderError

    with pytest.raises(ProviderError):
        activity.fetch_slack_activity(org_id, SINCE, workspace_id=workspace_id)


# --------------------------------------------------------------------------
# A rename must reach the next report without anyone pressing a button
# --------------------------------------------------------------------------


