"""Linear: update an issue; GitHub: label or assign an issue; Sheets: add rows.

Each changes something that already exists, so every value must be the
asker's own and resolved among what exists; nothing here touches the network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.actions import drive, github, linear
from app.actions.base import ActionContext, Audience
from app.core.exceptions import ActionError, ActionRejected

from .test_actions_more import TEAM, fake_linear


def _ctx(question):
    return ActionContext("org", None, "user", "a@x.com", question, "tok", (), None, {"kind": "web"})


# -- Linear -----------------------------------------------------------------------------


@pytest.fixture
def lin(monkeypatch):
    def gql(token, q, v=None, write=False):
        if "states {" in q:
            return {"team": {"states": {"nodes": [{"id": "s1", "name": "Todo"},
                                                  {"id": "s2", "name": "In Progress"}]}}}
        if "issue(id" in q:
            return {"issue": {"id": "i6", "identifier": "SYV-6", "title": "Search latency",
                              "url": "https://linear.app/x/SYV-6", "state": {"name": "Todo"}, "team": TEAM}}
        return fake_linear(q, v, write=write)

    monkeypatch.setattr(linear, "gql", gql)
    granted = {"scopes": ("read", "write")}
    monkeypatch.setattr("app.auth.user_connections.list_user_connections", lambda org, user: [
        SimpleNamespace(provider="linear", scopes=granted["scopes"])])
    return granted


def test_an_update_changes_only_what_was_asked(lin):
    p = linear.LinearUpdateIssue().prepare(
        {"issue": "SYV-6", "status": "in progress", "assignee": "Sajal", "labels": ["Bug"]},
        _ctx("move SYV-6 to In Progress, assign it to Sajal and add the Bug label"))
    assert p.payload["set"] == {"stateId": "s2", "assigneeId": "u2", "addedLabelIds": ["l1"]}
    assert p.payload["details"][0] == ["Status", "In Progress (was Todo)"]
    assert ["Add labels", "Bug"] in p.payload["details"]
    assert p.target.external_id == "i6" and "title" not in p.payload and "body" not in p.payload


@pytest.mark.parametrize("args, question, why", [
    ({"issue": "SYV-6", "status": "Done"}, "mark SYV-6 as Done", "can't find"),       # no such state
    ({"issue": "SYV-6", "status": "In Progress"}, "update SYV-6", "status"),         # not asked
    ({"issue": "SYV-6"}, "update SYV-6", "Say what to change"),
])
def test_an_update_the_asker_did_not_choose_is_refused(lin, args, question, why):
    with pytest.raises(ActionRejected, match=why):
        linear.LinearUpdateIssue().prepare(args, _ctx(question))


def test_an_update_needs_a_sign_in_that_allowed_it(lin):
    lin["scopes"] = ("read", "issues:create", "comments:create")
    with pytest.raises(ActionRejected, match="Reconnect Linear"):
        linear.LinearUpdateIssue().prepare({"issue": "SYV-6", "status": "Todo"}, _ctx("move SYV-6 to Todo"))


def test_write_scope_is_asked_only_when_updating_is_on(monkeypatch):
    monkeypatch.setattr("app.actions.policy.enabled_actions", lambda org, ws: {"linear.comment"})
    assert "write" not in linear.scopes_for("org").split(",")
    monkeypatch.setattr("app.actions.policy.enabled_actions", lambda org, ws: {"linear.update_issue"})
    assert linear.scopes_for("org").split(",")[-1] == "write"


def test_an_update_sends_issue_update_on_the_issue(monkeypatch):
    sent = []
    monkeypatch.setattr(linear, "gql", lambda token, q, v=None, write=False: sent.append((q, v, write)) or {
        "issueUpdate": {"success": True, "issue": {"id": "i6", "identifier": "SYV-6", "url": "https://l/x"}}})
    result = linear.LinearUpdateIssue().execute("k", {"external_id": "i6"}, {"set": {"stateId": "s2"}}, "tok")
    q, v, write = sent[0]
    assert "issueUpdate" in q and v == {"id": "i6", "input": {"stateId": "s2"}} and write
    assert result.external_id == "SYV-6"


# -- GitHub -----------------------------------------------------------------------------


@pytest.fixture
def gh(monkeypatch):
    state = {"access": {"pull": True, "triage": True}, "issue": {"title": "Flaky test",
             "html_url": "https://github.com/acme/handbook/issues/12"}, "posted": []}
    monkeypatch.setattr(github, "company_repo", lambda org, name: "acme/handbook")
    monkeypatch.setattr(github, "scoped_token", lambda token, full: "narrow")

    def call(method, path, token, *, json=None):
        if method == "POST":
            state["posted"].append((path, token, json))
            return {"assignees": [{"login": a} for a in (json or {}).get("assignees", [])
                                  if a != "ghost"]}
        if path == "/repos/acme/handbook":
            return {"private": True, "has_issues": True, "permissions": state["access"]}
        if path == "/repos/acme/handbook/issues/12":
            return state["issue"]
        if path.startswith("/repos/acme/handbook/labels"):
            return [{"name": "bug"}]
        if path.startswith("/repos/acme/handbook/assignees/"):
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(github, "_call", call)
    return state


def test_triage_access_may_label_and_assign_an_issue(gh):
    p = github.GitHubLabelAssign().prepare(
        {"issue": "handbook#12", "labels": ["Bug"], "assignees": ["@sana"]},
        _ctx("label handbook#12 as Bug and assign @sana"))
    assert p.payload["set"] == {"labels": ["bug"], "assignees": ["sana"]}
    assert p.audience == Audience(members=None)
    gh["access"] = {"pull": True}
    with pytest.raises(ActionRejected, match="triage access"):
        github.GitHubLabelAssign().prepare({"issue": "handbook#12", "labels": ["Bug"]},
                                           _ctx("label handbook#12 as Bug"))


def test_a_pull_request_or_an_empty_change_is_refused(gh):
    with pytest.raises(ActionRejected, match="Say which"):
        github.GitHubLabelAssign().prepare({"issue": "handbook#12"}, _ctx("tidy up handbook#12"))
    gh["issue"]["pull_request"] = {}
    with pytest.raises(ActionRejected, match="not pull requests"):
        github.GitHubLabelAssign().prepare({"issue": "handbook#12", "labels": ["Bug"]},
                                           _ctx("label handbook#12 as Bug"))


def test_labels_and_assignees_are_added_on_the_narrowed_token(gh):
    target = {"external_id": "acme/handbook#12", "url": "https://github.com/acme/handbook/issues/12"}
    github.GitHubLabelAssign().execute("k", target, {"set": {"labels": ["bug"], "assignees": ["sana"]}}, "wide")
    assert [(p, t) for p, t, _ in gh["posted"]] == [
        ("/repos/acme/handbook/issues/12/labels", "narrow"), ("/repos/acme/handbook/issues/12/assignees", "narrow")]
    with pytest.raises(ActionError, match="did not assign ghost"):
        github.GitHubLabelAssign().execute("k", target, {"set": {"assignees": ["ghost"]}}, "wide")


# -- Google Sheets ------------------------------------------------------------------------


class Reply:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body


@pytest.fixture
def sheets(monkeypatch):
    state = {"files": [{"id": "S1", "name": "Sales", "webViewLink": "https://docs.google.com/s/S1",
                        "capabilities": {"canEdit": True}}], "sent": []}

    def get(url, **kw):
        if url.endswith("/drive/v3/files"):
            state["q"] = kw["params"]["q"]
            return Reply({"files": state["files"]})
        if url.endswith("/permissions"):
            return Reply({"permissions": [{"type": "user", "emailAddress": "a@x.com"}]})
        raise AssertionError(url)

    def request(method, url, **kw):
        if method == "POST":
            state["sent"].append((url, kw["params"], kw["json"]))
            return Reply({})
        if url.endswith("/S1"):
            return Reply({"sheets": [{"properties": {"title": "Q4"}}]})
        if "/values/'Q4'!1:1" in url:
            return Reply({"values": [["Region", "Deals", "Revenue"]]})
        raise AssertionError(url)

    monkeypatch.setattr(drive.httpx, "get", get)
    monkeypatch.setattr(drive.httpx, "request", request)
    return state


def test_rows_go_under_the_sheets_own_header(sheets):
    action = drive.DriveAppendRows()
    p = action.prepare({"sheet": "Sales", "rows": [["North", "4", "=HYPERLINK(1)"], ["South", "2"]]},
                       _ctx("add these to the Sales sheet: North 4, South 2"))
    assert "name = 'Sales'" in sheets["q"]
    assert p.target.name == "Sales › Q4" and p.payload["header"] == ["Region", "Deals", "Revenue"]
    assert p.audience == Audience(members=("a@x.com",))
    action.execute("k", p.target.as_dict(), p.payload, "tok")
    url, params, body = sheets["sent"][0]
    assert url.endswith("/S1/values/'Q4'!A1:append") and params["valueInputOption"] == "RAW"
    assert body == {"values": [["North", "4", "=HYPERLINK(1)"], ["South", "2", ""]]}  # RAW: never a formula


def test_a_row_wider_than_the_sheet_or_a_changed_header_is_refused(sheets):
    action = drive.DriveAppendRows()
    with pytest.raises(ActionRejected, match="3 columns"):
        action.prepare({"sheet": "Sales", "rows": [["a", "b", "c", "d"]]}, _ctx("add a row to Sales"))
    p = action.prepare({"sheet": "Sales", "rows": [["North", "4", "9"]]}, _ctx("add a row to Sales"))
    with pytest.raises(ActionRejected, match="header row"):
        action.edit(p.payload, {"body": "| Area | Deals | Revenue |\n| --- | --- | --- |\n| N | 1 | 2 |"})
    edited = action.edit(p.payload, {"body": p.payload["body"].replace("North", "East")})
    assert drive._new_rows(edited["body"], p.payload["header"]) == [["East", "4", "9"]]


def test_only_a_sheet_handbook_can_reach_is_found(sheets):
    sheets["files"] = []
    with pytest.raises(ActionRejected, match="only to Sheets it created"):
        drive.DriveAppendRows().prepare({"sheet": "Budget", "rows": [["a"]]}, _ctx("add a row to Budget"))
    with pytest.raises(ActionRejected, match="Sheet"):
        drive.DriveAppendRows().prepare({"sheet": "Sales", "rows": [["a"]]}, _ctx("add a row please"))
