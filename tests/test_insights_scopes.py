"""Which scopes a member may chart, and how fresh each connector is.

Membership is the whole security boundary here: a space a member is not in
must not appear at all -- not merely be hidden by the UI, but never leave the
database. The join against ``workspace_members`` is what enforces that, so it
is asserted rather than assumed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import get_connection
from app.insights import scopes
from .conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"scopes-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _user(org_id) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO users (email, org_id, role) VALUES (%s, %s, 'member') RETURNING id",
            (f"{uuid.uuid4().hex[:8]}@example.com", org_id),
        ).fetchone()
        conn.commit()
    return str(row[0])


def _space(org_id, owner, name="Meeting notes", members=()) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO workspaces (org_id, name, created_by) VALUES (%s, %s, %s) RETURNING id",
            (org_id, name, owner),
        ).fetchone()
        space_id = str(row[0])
        for user_id, role in members:
            conn.execute(
                "INSERT INTO workspace_members (workspace_id, user_id, role) VALUES (%s, %s, %s)",
                (space_id, user_id, role),
            )
        conn.commit()
    return space_id


def _connection(org_id, provider, *, workspace_id=None, user_id=None,
                last_sync=None, needs_reauth=False):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO oauth_connections
                (org_id, provider, external_workspace_id, workspace_id,
                 connected_by_user_id, access_token_encrypted, last_sync_at,
                 needs_reauth)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (org_id, provider, uuid.uuid4().hex, workspace_id, user_id, "x",
             last_sync, needs_reauth),
        )
        conn.commit()


def test_the_company_scope_is_always_offered(org):
    """Even with nothing connected -- the empty state has to be able to say
    "connect something", which it cannot do if the scope itself is missing."""
    user = _user(org)
    found = scopes.member_scopes(org, user)
    assert [s.id for s in found] == [None], "the company scope is id=None"
    assert found[0].providers == []


def test_an_org_connection_appears_under_the_company_scope(org):
    user = _user(org)
    _connection(org, "notion", user_id=user)
    found = scopes.member_scopes(org, user)
    assert found[0].providers == ["notion"]


def test_a_space_the_member_belongs_to_is_offered(org):
    user = _user(org)
    space = _space(org, user, members=[(user, "owner")])
    _connection(org, "google", workspace_id=space, user_id=user)

    found = scopes.member_scopes(org, user)
    ids = [s.id for s in found]
    assert ids == [None, space]
    assert found[1].providers == ["google"]


def test_a_space_the_member_does_not_belong_to_never_leaves_the_database(org):
    """The membership join IS the boundary. A space whose charts would name
    colleagues the asker cannot see must not be offerable at all."""
    owner = _user(org)
    outsider = _user(org)
    space = _space(org, owner, members=[(owner, "owner")])
    _connection(org, "notion", workspace_id=space, user_id=owner)

    found = scopes.member_scopes(org, outsider)
    assert [s.id for s in found] == [None], "an outsider sees only the company"


def test_a_space_with_no_connections_is_still_offered(org):
    """An empty `providers` list is a real answer -- "this space has nothing
    connected yet". Dropping the space instead makes it silently vanish from
    the picker, which is how the scheduler's space list confused people."""
    user = _user(org)
    space = _space(org, user, members=[(user, "member")])

    found = scopes.member_scopes(org, user)
    assert [s.id for s in found] == [None, space]
    assert found[1].providers == []


def test_another_orgs_connection_is_never_counted(org, org_cleanup):
    user = _user(org)
    with get_connection() as conn:
        other = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"other-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(other[0]))
    _connection(str(other[0]), "notion")

    found = scopes.member_scopes(org, user)
    assert found[0].providers == []


def test_freshness_reports_when_each_connector_last_synced(org):
    """The highest-trust panel in the section: a number is worthless if nobody
    can tell whether it is current."""
    user = _user(org)
    synced = datetime.now(timezone.utc) - timedelta(hours=2)
    _connection(org, "notion", user_id=user, last_sync=synced)

    rows = scopes.freshness(org, user_id=user, workspace_id=None)
    assert len(rows) == 1
    assert rows[0].provider == "notion"
    assert abs((rows[0].last_sync_at - synced).total_seconds()) < 5
    assert rows[0].needs_reauth is False


def test_freshness_surfaces_a_dead_token_rather_than_an_old_date(org):
    """A connector with a dead token is not "stale", it is BROKEN, and auto-sync
    skips it entirely -- so waiting will never make it current. Saying only
    "last synced 6 days ago" invites someone to wait."""
    user = _user(org)
    _connection(org, "linear", user_id=user, needs_reauth=True,
                last_sync=datetime.now(timezone.utc) - timedelta(days=6))

    rows = scopes.freshness(org, user_id=user, workspace_id=None)
    assert rows[0].needs_reauth is True


def test_freshness_for_a_space_a_member_is_not_in_is_refused(org):
    """Not an empty list -- a refusal. An empty list reads as "that space has
    nothing connected", which is a different and misleading claim."""
    owner = _user(org)
    outsider = _user(org)
    space = _space(org, owner, members=[(owner, "owner")])

    from app.core.exceptions import AuthError

    with pytest.raises(AuthError):
        scopes.freshness(org, user_id=outsider, workspace_id=space)


def test_a_never_synced_connection_reports_none_not_a_date(org):
    """"Never synced" and "synced long ago" need different copy: the first is
    "waiting for the first sync", the second is "stale"."""
    user = _user(org)
    _connection(org, "slack", user_id=user, last_sync=None)

    rows = scopes.freshness(org, user_id=user, workspace_id=None)
    assert rows[0].last_sync_at is None
