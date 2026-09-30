"""Linear per-issue access (`sources.linear.LinearAdapter._access_for`).

A private team's issues were readable by everyone in the space that indexed
them. A non-public team's membership is now the issue's ACL, plus anyone the
issue was individually shared with (`Issue.sharedAccess`). The fake below
REJECTS any query it does not recognise, so a new per-issue call cannot slip
into the listing unnoticed -- the Slack `users.info` lesson (CLAUDE.md §5).
"""

from __future__ import annotations

import pytest

from app.sources.base import DocAccess
from app.sources.factory import ACL_CAPABLE
from app.sources.linear import LinearAdapter


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _issue(iid, team, shared=()):
    return {
        "id": iid, "identifier": f"ENG-{iid}", "title": "t", "url": f"u{iid}",
        "updatedAt": "2026-09-01T00:00:00Z", "team": {"id": team} if team else None,
        "sharedAccess": {"isShared": bool(shared),
                         "sharedWithUsers": [{"email": e} for e in shared]},
    }


def _fake(monkeypatch, *, issues, teams, members, viewer="bot@corp.com", fail=()):
    calls: list[str] = []

    def post(url, json, headers, timeout):
        query = json["query"]
        name = query.split("(")[0].split("{")[0].replace("query", "").strip() or "Viewer"
        calls.append(name)
        if name in fail:
            return _Resp({"errors": [{"message": "boom"}]})
        if name == "Issues":
            return _Resp({"data": {"issues": {"nodes": issues,
                                              "pageInfo": {"hasNextPage": False, "endCursor": None}}}})
        if name == "Teams":
            return _Resp({"data": {"teams": {
                "nodes": [{"id": k, "visibility": v} for k, v in teams.items()],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}})
        if name == "TeamMembers":
            team = json["variables"]["id"]
            return _Resp({"data": {"team": {"members": {
                "nodes": [{"email": e} for e in members.get(team, [])],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}})
        if name == "Viewer":
            return _Resp({"data": {"viewer": {"email": viewer}}})
        raise AssertionError(f"unexpected Linear query: {name}")

    monkeypatch.setattr("app.sources.linear.httpx.post", post)
    return calls


def _access(monkeypatch, **kw) -> tuple[dict[str, DocAccess | None], list[str]]:
    calls = _fake(monkeypatch, **kw)
    refs = LinearAdapter(token="tok").list_documents()
    return {r.external_id: r.access for r in refs}, calls


def test_linear_is_acl_capable():
    assert "linear" in ACL_CAPABLE


def test_a_public_team_is_scope_public_and_reads_no_members(monkeypatch):
    access, calls = _access(
        monkeypatch, issues=[_issue("1", "pub")], teams={"pub": "public"}, members={}
    )
    assert access["1"] == DocAccess.scope_public()
    assert "TeamMembers" not in calls


def test_a_private_team_is_its_members_plus_shared_users(monkeypatch):
    access, _ = _access(
        monkeypatch,
        issues=[_issue("1", "sec", shared=("guest@corp.com",))],
        teams={"sec": "private"},
        members={"sec": ["ada@corp.com", "bo@corp.com"]},
    )
    assert access["1"] == DocAccess.restricted(["ada@corp.com", "bo@corp.com", "guest@corp.com"])


def test_a_restricted_sub_team_is_treated_as_members_only(monkeypatch):
    access, _ = _access(
        monkeypatch, issues=[_issue("1", "sub")], teams={"sub": "restricted"},
        members={"sub": ["ada@corp.com"]},
    )
    assert access["1"] == DocAccess.restricted(["ada@corp.com"])


def test_membership_is_read_once_per_team_not_per_issue(monkeypatch):
    _, calls = _access(
        monkeypatch,
        issues=[_issue("1", "sec"), _issue("2", "sec"), _issue("3", "sec")],
        teams={"sec": "private"}, members={"sec": ["ada@corp.com"]},
    )
    assert calls.count("Teams") == 1
    assert calls.count("TeamMembers") == 1


def test_unreadable_membership_falls_back_to_the_connected_account(monkeypatch):
    """Owner-only, exactly as Drive: indexed for the one account we can prove
    reads it, reported as unreadable so the pipeline freezes an indexed copy."""
    access, _ = _access(
        monkeypatch, issues=[_issue("1", "sec")], teams={"sec": "private"},
        members={}, fail=("TeamMembers",),
    )
    assert access["1"] == DocAccess.owner_only("bot@corp.com")
    assert access["1"].unreadable


def test_an_unlisted_team_is_never_assumed_public(monkeypatch):
    access, _ = _access(
        monkeypatch, issues=[_issue("1", "ghost")], teams={}, members={},
    )
    assert access["1"] == DocAccess.owner_only("bot@corp.com")


def test_no_team_and_no_account_means_skip(monkeypatch):
    access, _ = _access(
        monkeypatch, issues=[_issue("1", "sec")], teams={"sec": "private"},
        members={}, fail=("TeamMembers", "Viewer"),
    )
    assert access["1"] is None


@pytest.mark.parametrize("visibility", ["private", "restricted"])
def test_a_team_failure_does_not_widen_anything(monkeypatch, visibility):
    access, _ = _access(
        monkeypatch, issues=[_issue("1", "t")], teams={"t": visibility},
        members={}, fail=("Teams",),
    )
    assert not access["1"].is_public
