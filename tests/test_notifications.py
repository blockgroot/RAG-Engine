"""The attention list shows only what the caller can actually fix.

An expired token stops a sync silently, and until this existed the only place
that said so was a card nobody had a reason to open. The rules worth pinning
are about SCOPE, not about the wording: an org-wide problem is an admin's, a
space's is its owner's, and a member of a broken space sees nothing -- a
notification you cannot act on is an alarm with no off switch.
"""

from __future__ import annotations

from datetime import datetime

from app.api import notifications as notif
from app.auth.credentials import OAuthConnectionInfo
from app.auth.session import SessionClaims
from app.workspaces.store import WorkspaceInfo


def _conn(provider, *, needs_reauth=False, config=None):
    return OAuthConnectionInfo(
        id="c1",
        provider=provider,
        external_workspace_id="T1",
        external_workspace_name="Acme",
        created_at=datetime(2026, 1, 1),
        source_config=config,
        needs_reauth=needs_reauth,
    )


def _session(role="admin"):
    return SessionClaims(
        user_id="u1", org_id="org", role=role, issued_at=datetime(2026, 1, 1)
    )


def _patch(monkeypatch, *, by_scope, spaces=()):
    monkeypatch.setattr(
        notif, "list_connections", lambda org, ws=None: by_scope.get(ws, [])
    )
    monkeypatch.setattr(notif, "list_my_workspaces", lambda org, uid: list(spaces))


def test_an_expired_connection_is_reported_to_the_admin(monkeypatch):
    _patch(monkeypatch, by_scope={None: [_conn("google", needs_reauth=True)]})
    result = notif.list_notifications(session=_session())
    assert result["count"] == 1
    item = result["items"][0]
    assert item["kind"] == "reauth"
    assert item["severity"] == "high"
    assert item["href"] == "/admin/connections"
    assert "Google Drive" in item["title"]


def test_a_member_is_told_nothing_about_the_company(monkeypatch):
    """They cannot reconnect it, so naming it is noise."""
    _patch(monkeypatch, by_scope={None: [_conn("notion", needs_reauth=True)]})
    assert notif.list_notifications(session=_session("member"))["count"] == 0


def test_a_space_owner_is_told_about_their_own_space(monkeypatch):
    space = WorkspaceInfo(
        id="ws-1", org_id="org", name="Meeting notes", created_by="u1", role="owner"
    )
    _patch(
        monkeypatch,
        by_scope={"ws-1": [_conn("google", needs_reauth=True)]},
        spaces=[space],
    )
    item = notif.list_notifications(session=_session("member"))["items"][0]
    assert item["scope"] == "Meeting notes"
    assert item["href"] == "/workspaces/ws-1"


def test_a_space_MEMBER_is_not(monkeypatch):
    """Every control on the space page is disabled for them."""
    space = WorkspaceInfo(
        id="ws-1", org_id="org", name="Meeting notes", created_by="u9", role="member"
    )
    _patch(
        monkeypatch,
        by_scope={"ws-1": [_conn("google", needs_reauth=True)]},
        spaces=[space],
    )
    assert notif.list_notifications(session=_session("member"))["count"] == 0


def test_connected_but_indexing_nothing_is_its_own_item(monkeypatch):
    """Drive with no folder and Slack with no channels both read as 'Linked'."""
    _patch(
        monkeypatch,
        by_scope={None: [_conn("google", config={}), _conn("slack", config={})]},
    )
    kinds = {i["kind"] for i in notif.list_notifications(session=_session())["items"]}
    assert kinds == {"scope"}


def test_a_scoped_healthy_connection_reports_nothing(monkeypatch):
    _patch(
        monkeypatch,
        by_scope={
            None: [
                _conn("google", config={"folder_id": "f1"}),
                _conn("slack", config={"channel_ids": ["C1"]}),
                _conn("notion"),
            ]
        },
    )
    assert notif.list_notifications(session=_session())["count"] == 0


def test_an_expired_connection_is_not_ALSO_reported_as_unscoped(monkeypatch):
    """One connection is one problem: reconnecting is what unblocks the rest."""
    _patch(monkeypatch, by_scope={None: [_conn("google", needs_reauth=True, config={})]})
    assert notif.list_notifications(session=_session())["count"] == 1


def test_expired_sorts_above_unconfigured(monkeypatch):
    _patch(
        monkeypatch,
        by_scope={
            None: [_conn("slack", config={}), _conn("notion", needs_reauth=True)]
        },
    )
    assert [i["severity"] for i in notif.list_notifications(session=_session())["items"]] == [
        "high",
        "medium",
    ]
