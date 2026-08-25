"""Space-scoped schedulers read the SPACE's connection, never the org's.

The fetchers already took a ``workspace_id``, and the API now stores one per
scheduler — but "it is threaded through" is exactly the kind of claim that is
true in three places and quietly false in the fourth. These tests save BOTH an
org-wide and a workspace connection for the same provider, with *different*
tokens, and assert each fetcher resolves the workspace one.

Real Postgres (credential resolution is the subject — that is where a scope
mistake would live), but no network: the adapter/reader factories are stubbed
to record what they were handed.
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


def _connect_both(org_id: str, workspace_id: str, provider: str) -> None:
    save_connection(org_id, provider, _tokens(ORG_TOKEN))
    save_connection(org_id, provider, _tokens(SPACE_TOKEN), workspace_id=workspace_id)


# --------------------------------------------------------------------------
# Slack
# --------------------------------------------------------------------------


@requires_db
def test_slack_in_a_space_reads_that_spaces_connection(org_and_space, monkeypatch):
    org_id, _, workspace_id = org_and_space
    _connect_both(org_id, workspace_id, "slack")
    # Distinct channel scope per connection: a report that read the company's
    # channels from inside a space is the leak this scoping prevents.
    set_connection_config(org_id, "slack", {"channel_ids": ["C-ORG"]})
    set_connection_config(
        org_id, "slack", {"channel_ids": ["C-SPACE"]}, workspace_id=workspace_id
    )

    seen: dict = {}

    class _Adapter:
        def fetch_recent_messages(self, since, *, max_messages=300):
            return [], []

        def channel_labels(self):
            return ["space-only"]

    def _build(source_type, *, token=None, config=None, **kwargs):
        seen["token"] = token
        seen["config"] = config
        return _Adapter()

    monkeypatch.setattr("app.sources.build_source_adapter", _build)

    activity.fetch_slack_activity(org_id, SINCE, workspace_id=workspace_id)

    assert seen["token"] == SPACE_TOKEN
    assert seen["config"]["channel_ids"] == ["C-SPACE"]


@requires_db
def test_slack_without_a_space_still_reads_the_org_connection(
    org_and_space, monkeypatch
):
    """The org-wide path must be untouched by the space plumbing."""
    org_id, _, workspace_id = org_and_space
    _connect_both(org_id, workspace_id, "slack")
    set_connection_config(org_id, "slack", {"channel_ids": ["C-ORG"]})

    seen: dict = {}

    class _Adapter:
        def fetch_recent_messages(self, since, *, max_messages=300):
            return [], []

        def channel_labels(self):
            return ["org-wide"]

    monkeypatch.setattr(
        "app.sources.build_source_adapter",
        lambda source_type, *, token=None, config=None, **kw: (
            seen.update(token=token, config=config) or _Adapter()
        ),
    )

    activity.fetch_slack_activity(org_id, SINCE)

    assert seen["token"] == ORG_TOKEN
    assert seen["config"]["channel_ids"] == ["C-ORG"]


# --------------------------------------------------------------------------
# Linear
# --------------------------------------------------------------------------


@requires_db
def test_linear_in_a_space_reads_that_spaces_connection(org_and_space, monkeypatch):
    """Linear carries no per-connection config, so the token IS the scope."""
    org_id, _, workspace_id = org_and_space
    _connect_both(org_id, workspace_id, "linear")

    seen: dict = {}

    class _Adapter:
        def fetch_recent_issues(self, since, *, max_issues=300):
            return []

    monkeypatch.setattr(
        "app.sources.build_source_adapter",
        lambda source_type, *, token=None, config=None, **kw: (
            seen.update(token=token) or _Adapter()
        ),
    )

    activity.fetch_linear_activity(org_id, SINCE, workspace_id=workspace_id)

    assert seen["token"] == SPACE_TOKEN


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
