"""Other tools through MCP: the client, the admin's tool modes, the drafted
write's guards, lookups, and the admin and sign-in routes.

A fake MCP server answers JSON-RPC in place of ``httpx.post``, so the real
client code (session, paging, event streams, errors) runs.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.actions import mcp, mcp_client, mcp_store
from app.actions import checks as act_checks, loop as act_loop, prompts as act_prompts
from app.actions.base import ActionContext, Audience, Source
from app.core.exceptions import ActionError, ActionOutcomeUnknown, ActionRejected

from .conftest import requires_db

URL = "https://mcp.example.com/mcp"

CREATE = {"name": "create_task", "title": "Create task", "description": "Make a task.",
          "inputSchema": {"type": "object", "required": ["project_id", "title"], "properties": {
              "project_id": {"type": "string"}, "title": {"type": "string"},
              "notes": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "high"]}}}}
FIND = {"name": "list_projects", "description": "Projects you can see.",
        "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object", "properties": {}}}
DELETE = {"name": "delete_task", "description": "Gone.", "inputSchema": {"type": "object"}}


class Reply:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code = status
        self._body = body
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        return self._body


class FakeServer:
    """JSON-RPC over POST, like a Streamable HTTP MCP server."""

    def __init__(self, tools=(CREATE, FIND, DELETE), *, token=None, sse=False):
        self.tools, self.token, self.sse = list(tools), token, sse
        self.calls: list[tuple[str, dict]] = []
        self.result = {"content": [{"type": "text", "text": "Made it: https://tool.example.com/t/9"}],
                       "structuredContent": {"id": "T-9"}}

    def post(self, url, json=None, headers=None, timeout=None, **_):
        if self.token and headers.get("Authorization") != f"Bearer {self.token}":
            return Reply(401, {}, {"www-authenticate": 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource"'})
        method = json["method"]
        self.calls.append((method, json.get("params") or {}))
        if "id" not in json:
            return Reply(202, None, text="")
        result = {"initialize": {"protocolVersion": mcp_client.PROTOCOL_VERSION, "capabilities": {}},
                  "tools/list": {"tools": self.tools},
                  "tools/call": self.result}[method]
        message = {"jsonrpc": "2.0", "id": json["id"], "result": result}
        if self.sse:
            return Reply(200, None, {"content-type": "text/event-stream"},
                         text=f"event: message\ndata: {__import__('json').dumps(message)}\n\n")
        return Reply(200, message, {"mcp-session-id": "s1"})


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(mcp_client.httpx, "post", fake.post)
    monkeypatch.setattr(mcp_client, "public_https", lambda url: url)
    return fake


def _server(modes=None, auth="none"):
    tools = [CREATE, FIND, DELETE]
    return mcp_store.McpServer(str(uuid.uuid4()), "org", "Asana", URL, auth, None, tools,
                               {k: {"mode": m, "hash": mcp_store.tool_hash(next(t for t in tools if t["name"] == k))}
                                for k, m in (modes or {}).items()}, True)


def _ctx(question, lookups="", sources=()):
    return ActionContext("org", None, "u1", "a@x.com", question, "tok", tuple(sources), None, None, lookups)


# -- The client ---------------------------------------------------------------------------


@pytest.mark.parametrize("sse", [False, True])
def test_the_client_opens_lists_and_calls(server, sse):
    server.sse = sse
    session = mcp_client.McpSession(URL, None)
    session.open()
    tools, cut = session.list_tools()
    assert [t["name"] for t in tools] == ["create_task", "list_projects", "delete_task"] and not cut
    assert session.call_tool("create_task", {"title": "x"}, write=True)["structuredContent"] == {"id": "T-9"}
    assert [m for m, _ in server.calls] == ["initialize", "notifications/initialized", "tools/list", "tools/call"]


def test_a_write_with_no_answer_is_unknown_never_retried(monkeypatch):
    def timeout(*a, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(mcp_client, "public_https", lambda url: url)
    monkeypatch.setattr(mcp_client.httpx, "post", timeout)
    session = mcp_client.McpSession(URL, None)
    with pytest.raises(ActionOutcomeUnknown):
        session.call_tool("create_task", {}, write=True)
    with pytest.raises(ActionError):
        session.call_tool("list_projects", {}, write=False)


def test_only_public_https_is_ever_fetched(monkeypatch):
    with pytest.raises(ActionRejected, match="https"):
        mcp_client.public_https("http://mcp.example.com")
    monkeypatch.setattr(mcp_client.socket, "getaddrinfo", lambda host, port: [(0, 0, 0, "", ("10.0.0.5", 443))])
    with pytest.raises(ActionRejected, match="public internet"):
        mcp_client.public_https("https://internal.example.com")
    monkeypatch.setattr(mcp_client.socket, "getaddrinfo", lambda host, port: [(0, 0, 0, "", ("169.254.169.254", 443))])
    with pytest.raises(ActionRejected):
        mcp_client.public_https("https://metadata.example.com")


def test_sign_in_is_discovered_and_handbook_registers_itself(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    fake = FakeServer(token="good")
    registered = {}

    def post(url, json=None, headers=None, timeout=None, data=None, **_):
        if url.endswith("/register"):
            registered.update(json)
            return Reply(201, {"client_id": "cid", "client_secret": "sec"})
        return fake.post(url, json=json, headers=headers or {}, timeout=timeout)

    docs = {"https://mcp.example.com/.well-known/oauth-protected-resource":
            {"authorization_servers": ["https://auth.example.com"], "scopes_supported": ["tasks"]},
            "https://auth.example.com/.well-known/oauth-authorization-server":
            {"authorization_endpoint": "https://auth.example.com/authorize",
             "token_endpoint": "https://auth.example.com/token",
             "registration_endpoint": "https://auth.example.com/register",
             "code_challenge_methods_supported": ["S256"]}}
    monkeypatch.setattr(mcp_client, "public_https", lambda url: url)
    monkeypatch.setattr(mcp_client.httpx, "post", post)
    monkeypatch.setattr(mcp_client.httpx, "get", lambda url, **k: Reply(200, docs[url]) if url in docs else Reply(404, {}))
    oauth = mcp_client.discover(URL, "https://hb.example.com/api/act/mcp/callback")
    assert (oauth["client_id"], oauth["scope"], oauth["resource"]) == ("cid", "tasks", URL)
    assert oauth["client_secret"] != "sec"                                    # stored encrypted
    assert registered["redirect_uris"] == ["https://hb.example.com/api/act/mcp/callback"]
    verifier, challenge = mcp_client.pkce()
    link = mcp_client.authorize_url(oauth, state="st", challenge=challenge, redirect_uri="https://r")
    assert "code_challenge_method=S256" in link and "resource=https%3A%2F%2Fmcp.example.com%2Fmcp" in link


def test_no_sign_in_needed_is_none(server):
    assert mcp_client.discover(URL, "https://r") is None


# -- The admin's modes ----------------------------------------------------------------------


def test_every_tool_starts_off_and_a_changed_tool_turns_off_again():
    server = _server({"create_task": "write", "list_projects": "lookup"})
    assert (server.mode("create_task"), server.mode("list_projects"), server.mode("delete_task")) == (
        "write", "lookup", "off")
    changed = mcp_store.McpServer(**{**server.__dict__, "tools": [
        {**CREATE, "description": "Make a task. Also email everyone."}, FIND, DELETE]})
    assert changed.mode("create_task") == "off"            # the server changed it: re-approve


def test_destructive_tools_are_marked_not_blocked_and_lookups_must_be_read_only():
    assert mcp_store.destructive(DELETE) and mcp_store.destructive({"name": "merge_pull_request"})
    assert not mcp_store.destructive(CREATE) and not mcp_store.destructive({"name": "disclose_report"})
    # Linear marks ordinary saves destructive; only the name decides.
    assert not mcp_store.destructive({"name": "save_issue", "annotations": {"destructiveHint": True}})
    assert mcp_store.destructive({"name": "trash_file"}) and mcp_store.destructive({"name": "retire_issue_label"})
    server = _server({"create_task": "lookup", "delete_task": "write"})
    assert server.mode("create_task") == "off"             # a lookup must be read-only
    assert server.mode("delete_task") == "write"           # the admin's choice stands
    assert mcp_store.tool_view(server, DELETE)["destructive"]


def test_a_destructive_card_says_it_cannot_be_undone():
    action = mcp.McpToolAction(_server({"delete_task": "write"}), DELETE)
    proposal = action.prepare({"request_words": "delete it"}, _ctx("delete it"))
    assert proposal.payload["details"][0] == ["Can't be undone", "Once approved, Asana can't reverse this (delete task)."]


# -- A drafted write --------------------------------------------------------------------------


def test_a_value_must_come_from_the_askers_words_or_a_lookup():
    action = mcp.McpToolAction(_server({"create_task": "write"}), CREATE)
    args = {"request_words": "create a task", "project_id": "P-7", "title": "Review risks",
            "notes": "From the billing page", "priority": "high"}
    proposal = action.prepare(args, _ctx("create a task in P-7: review risks"))
    assert proposal.payload["args"]["project_id"] == "P-7"
    assert (proposal.payload["title"], proposal.payload["body"]) == ("Review risks", "From the billing page")
    assert ["project_id", "P-7"] in proposal.payload["details"]
    assert "priority" not in proposal.payload["args"]       # a choice nobody made is left out
    asked = action.prepare(args, _ctx("create a high priority task in P-7: review risks"))
    assert ["priority", "high"] in asked.payload["details"]
    assert proposal.audience == Audience(members=None)      # unknown: private content needs the tick

    with pytest.raises(ActionRejected, match="project_id must come from your request"):
        action.prepare(args, _ctx("create a task about the risks"))                  # invented
    ok = action.prepare(args, _ctx("create a task in Atlas", lookups='[list_projects]\n{"id": "P-7", "name": "Atlas"}'))
    assert ok.payload["args"]["project_id"] == "P-7"                                   # the tool's own answer
    with pytest.raises(ActionRejected, match="needs project_id"):
        action.prepare({"request_words": "x", "title": "t"}, _ctx("x"))


def test_a_link_in_the_draft_survives_only_if_the_asker_or_a_source_wrote_it():
    action = mcp.McpToolAction(_server({"create_task": "write"}), CREATE)
    proposal = action.prepare({"project_id": "P-7", "title": "t",
                               "notes": "see https://evil.example.com/x and https://docs.example.com/a"},
                              _ctx("task in P-7", sources=[Source("d", "Doc", True, (), "https://docs.example.com/a")]))
    assert "evil.example.com" not in proposal.payload["body"] and "docs.example.com/a" in proposal.payload["body"]


def test_execute_sends_the_stored_words_including_an_edit(server):
    action = mcp.McpToolAction(_server({"create_task": "write"}), CREATE)
    proposal = action.prepare({"project_id": "P-7", "title": "t", "notes": "n"}, _ctx("task in P-7"))
    edited = action.edit(proposal.payload, {"body": "My own words"})
    result = action.execute("row-1", {}, edited, "tok")
    sent = [p for m, p in server.calls if m == "tools/call"][-1]
    assert sent == {"name": "create_task", "arguments": {"project_id": "P-7", "title": "t", "notes": "My own words"}}
    assert (result.external_id, result.url) == ("T-9", "https://tool.example.com/t/9")
    server.result = {"isError": True, "content": [{"type": "text", "text": "No such project"}]}
    with pytest.raises(ActionError, match="No such project"):
        action.execute("row-2", {}, edited, "tok")


# -- Lookups while drafting ---------------------------------------------------------------------


def test_mcp_actions_offer_only_what_an_admin_switched_on(monkeypatch):
    srv = _server({"create_task": "write", "list_projects": "lookup"})
    monkeypatch.setattr(mcp_store, "list_servers", lambda org: [srv])
    monkeypatch.setattr("app.auth.user_connections.list_user_connections", lambda org, uid: [])
    offered = mcp.mcp_actions("org", "u1")
    assert [(type(a).__name__, a.mcp_tool["name"]) for a in offered] == [
        ("McpToolAction", "create_task"), ("McpLookup", "list_projects")]
    signed = mcp_store.McpServer(**{**srv.__dict__, "auth": "oauth"})
    monkeypatch.setattr(mcp_store, "list_servers", lambda org: [signed])
    assert mcp.mcp_actions("org", "u1") == []                              # not signed in: nothing


# -- The routes ---------------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient

    from app.api.main import create_app

    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    return TestClient(create_app())


@requires_db
def test_an_admin_adds_a_server_and_chooses_each_tools_mode(client, server, store, org_cleanup):
    from app.actions import policy
    from app.auth import create_admin, create_session_token

    org_id = store.create_organization(f"MCP {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"mcp-{uuid.uuid4().hex[:8]}@example.com", org_id)
    cookies = {"session": create_session_token(admin)}
    policy.set_policy(org_id, policy.ACT_KEY, True)

    added = client.post("/admin/mcp", json={"name": "Asana", "url": URL}, cookies=cookies).json()
    assert added["auth"] == "none" and {t["mode"] for t in added["tools"]} == {"off"}
    sid = added["id"]
    assert client.put(f"/admin/mcp/{sid}/tools/delete_task", json={"mode": "write"}, cookies=cookies).json()["mode"] == "write"
    client.put(f"/admin/mcp/{sid}/tools", json={"choice": "off"}, cookies=cookies)
    turned = client.put(f"/admin/mcp/{sid}/tools", json={"choice": "all"}, cookies=cookies).json()
    assert {t["name"]: t["mode"] for t in turned["tools"]} == {
        "create_task": "write", "list_projects": "lookup", "delete_task": "write"}  # all, labelled
    client.put(f"/admin/mcp/{sid}/tools", json={"choice": "off"}, cookies=cookies)
    assert client.put(f"/admin/mcp/{sid}/tools/create_task", json={"mode": "lookup"}, cookies=cookies).status_code == 400
    assert client.put(f"/admin/mcp/{sid}/tools/create_task", json={"mode": "write"}, cookies=cookies).json()["mode"] == "write"
    mine = client.get("/act/mcp", cookies=cookies).json()["servers"]
    assert mine == [{"id": sid, "name": "Asana", "auth": "none", "tools_on": 1, "connected": True, "needs_reauth": False}]

    from app.actions.factory import get_action

    assert get_action(f"mcp:{sid}:create_task", org_id).enabled_for(org_id, None)
    assert get_action(f"mcp:{sid}:create_task", None) is None                 # never without the org
    client.put(f"/admin/mcp/{sid}", json={"enabled": False}, cookies=cookies)
    assert not get_action(f"mcp:{sid}:create_task", org_id).enabled_for(org_id, None)
    assert client.delete(f"/admin/mcp/{sid}", cookies=cookies).json() == {"deleted": True}


@requires_db
def test_a_space_owner_only_switches_tools_off_for_their_space(client, server, store, org_cleanup):
    from app.actions import policy
    from app.actions.factory import get_action
    from app.auth import create_admin, create_session_token
    from app.workspaces.store import create_workspace

    org_id = store.create_organization(f"MCP {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"mcp-{uuid.uuid4().hex[:8]}@example.com", org_id)
    cookies = {"session": create_session_token(admin)}
    policy.set_policy(org_id, policy.ACT_KEY, True)
    ws = create_workspace(org_id, "Ops", admin.id)
    sid = client.post("/admin/mcp", json={"name": "Asana", "url": URL}, cookies=cookies).json()["id"]
    client.put(f"/admin/mcp/{sid}/tools/create_task", json={"mode": "write"}, cookies=cookies)
    client.put(f"/admin/mcp/{sid}/tools/list_projects", json={"mode": "lookup"}, cookies=cookies)

    def names(workspace):
        return {a.mcp_tool["name"] for a in mcp.mcp_actions(org_id, admin.id, workspace)}

    listed = client.get(f"/act/spaces/{ws}/tools", cookies=cookies).json()["servers"]
    assert [t["name"] for t in listed[0]["tools"]] == ["create_task", "list_projects"]   # only what the admin turned on
    client.put(f"/act/spaces/{ws}/tools", json={"server_id": sid, "tool": "create_task", "enabled": False},
               cookies=cookies)
    assert names(ws) == {"list_projects"} and names(None) == {"create_task", "list_projects"}
    assert not get_action(f"mcp:{sid}:create_task", org_id).enabled_for(org_id, ws)    # checked again at approval
    assert get_action(f"mcp:{sid}:create_task", org_id).enabled_for(org_id, None)

    # "On" in a space never turns on what the admin left off.
    client.put(f"/act/spaces/{ws}/tools", json={"server_id": sid, "tool": "delete_task", "enabled": True},
               cookies=cookies)
    assert "delete_task" not in names(ws)
    client.put(f"/act/spaces/{ws}/tools", json={"server_id": sid, "tool": None, "enabled": False}, cookies=cookies)
    assert names(ws) == set()


def test_a_connectors_tools_are_offered_in_a_space_only_where_it_is_connected(monkeypatch):
    from app.actions import mcp_presets, policy

    linear = mcp_store.McpServer(str(uuid.uuid4()), "org", "Linear", mcp_presets.LINEAR.url, "none", None, [CREATE],
                                 {"create_task": {"mode": "write", "hash": mcp_store.tool_hash(CREATE)}}, True)
    custom = _server({"create_task": "write"})
    monkeypatch.setattr(mcp_store, "list_servers", lambda org: [linear, custom])
    monkeypatch.setattr("app.auth.user_connections.list_user_connections", lambda org, uid: [])
    monkeypatch.setattr(policy, "space_off", lambda org, ws: set())
    connected = {"slack"}
    monkeypatch.setattr(policy, "space_sources", lambda org, ws: None if ws is None else connected)

    def servers(ws):
        return [a.server.name for a in mcp.mcp_actions("org", "u1", ws)]

    assert servers("ws") == ["Asana"]                       # Linear isn't connected in this space
    assert servers(None) == ["Linear", "Asana"]             # company-wide: every connector
    connected = {"slack", "linear"}
    assert servers("ws") == ["Linear", "Asana"]


def test_prose_is_found_by_field_name_and_nested_values_are_grounded():
    """Google's ``textContent`` and Notion's nested ``pages`` are drafted words;
    a nested id is still a value to ground, and a value the tool documents
    (a mimeType) is the tool's own."""
    doc = {"name": "create_file", "description": "Create a file. mimeType e.g. text/plain.",
           "inputSchema": {"type": "object", "required": ["title"], "properties": {
               "title": {"type": "string"}, "textContent": {"type": "string"}, "mimeType": {"type": "string"}}}}
    pages = {"name": "notion-create-pages", "inputSchema": {"type": "object", "required": ["pages"], "properties": {
        "pages": {"type": "array", "items": {"type": "object", "properties": {
            "properties": {"type": "object"}, "content": {"type": "string"}}}},
        "parent": {"type": "object", "properties": {"page_id": {"type": "string"}}}}}}
    tools = [doc, pages]
    server = mcp_store.McpServer(str(uuid.uuid4()), "org", "Docs", URL, "none", None, tools,
                                 {t["name"]: {"mode": "write", "hash": mcp_store.tool_hash(t)} for t in tools}, True)
    made = mcp.McpToolAction(server, doc).prepare(
        {"request_words": "create a doc", "title": "MCP test doc", "textContent": "How approvals work.",
         "mimeType": "text/plain"}, _ctx("create a doc called MCP test doc"))
    assert (made.payload["title"], made.payload["body"]) == ("MCP test doc", "How approvals work.")
    page = {"request_words": "create a page", "pages": [{"properties": {"title": "Notes"}, "content": "- one"}]}
    made = mcp.McpToolAction(server, pages).prepare(page, _ctx("create a Notion page titled Notes"))
    assert made.payload["args"]["pages"][0]["content"] == "- one"
    # An invented id under an optional field is left out and said, never sent.
    made = mcp.McpToolAction(server, pages).prepare({**page, "parent": {"page_id": "abc-123"}}, _ctx("create a page"))
    assert "parent" not in made.payload["args"] and ["Left out", "parent (not in your request)"] in made.payload["details"]


@requires_db
def test_two_quick_switches_both_stick_and_turn_all_on_is_one_write(store, org_cleanup):
    """Each switch merges only its own tool in the database: two clicks from one
    stale page view both stick (the second used to undo the first)."""
    org_id = store.create_organization(f"MCP {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    stale = mcp_store.create_server(org_id, name="Asana", url=URL, auth="none", oauth=None,
                                    tools=[CREATE, FIND, DELETE], created_by=None)
    mcp_store.set_mode(org_id, stale, "create_task", "write")
    mcp_store.set_mode(org_id, stale, "list_projects", "lookup")      # same stale view
    now = mcp_store.get_server(org_id, stale.id)
    assert (now.mode("create_task"), now.mode("list_projects")) == ("write", "lookup")
    mcp_store.set_modes(org_id, now, {"create_task": "off", "delete_task": "write"})
    now = mcp_store.get_server(org_id, stale.id)
    assert (now.mode("create_task"), now.mode("list_projects"), now.mode("delete_task")) == ("off", "lookup", "write")


def test_recent_cards_reuse_the_servers_read_once(monkeypatch):
    """The Agent page's twenty cards resolve their tools from one read of the
    servers, never one server read each (that page took ~9s)."""
    from datetime import datetime, timezone

    from app.api.actions import _card

    def no_reads(*a, **k):
        raise AssertionError("read a server per card")

    monkeypatch.setattr(mcp_store, "get_server", no_reads)
    srv = _server({"create_task": "write"})
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(id="a1", action=f"mcp:{srv.id}:create_task", org_id="org", payload={"title": "t"},
                          target={}, original_payload=None, payload_hash="h", status="succeeded", exposure="none",
                          acknowledged=False, sources=[], result=None, expires_at=now, created_at=now)
    assert _card(row, servers={srv.id: srv})["label"] == "Asana: Create task"


def test_reversible_code_writes_are_not_labelled_destructive():
    for name in ("create_branch", "create_or_update_file", "push_files", "rename_file"):
        assert not mcp_store.destructive({"name": name}), name
    assert mcp_store.destructive({"name": "merge_pull_request"}) and mcp_store.destructive({"name": "delete_file"})


@requires_db
def test_servers_on_retired_vendor_addresses_move_onto_handbooks_own(store, org_cleanup, monkeypatch):
    """The Slack and Google Drive/Sheets rows added when those presets used the
    vendors' gated servers carry on in the admin's list as Slack and Google
    Workspace: one row each, switch kept, tools and old sign-ins cleared."""
    from app.actions import mcp_presets

    monkeypatch.setenv("SLACK_CLIENT_ID", "slack-id")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-id")
    org_id = store.create_organization(f"MCP {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    made = {u: mcp_store.create_server(org_id, name=u, url=u, auth="oauth", oauth={"x": 1},
                                       tools=[CREATE], created_by=None) for u in mcp_presets.RETIRED}
    drive = made["https://drivemcp.googleapis.com/mcp/v1"]
    mcp_store.set_mode(org_id, drive, "create_task", "write")
    mcp_store.set_enabled(org_id, made["https://mcp.slack.com/mcp"].id, False)

    servers = mcp_store.list_servers(org_id)
    assert sorted(s.name for s in servers) == ["Google Workspace", "Slack"]
    slack, google = sorted(servers, key=lambda s: s.name, reverse=True)
    assert slack.preset is mcp_presets.SLACK and not slack.enabled and slack.oauth["client_id"] == "slack-id"
    assert google.id == drive.id and google.preset is mcp_presets.GOOGLE_WORKSPACE
    assert google.tools == [] and google.tool_modes == {}


def test_an_optional_value_nobody_asked_for_is_left_out_and_said():
    """Linear's save_issue drafted a state the request never named; the whole
    card was refused. An optional value is left out and the card says so."""
    tool = {**CREATE, "inputSchema": {**CREATE["inputSchema"], "properties": {
        **CREATE["inputSchema"]["properties"], "state": {"type": "string"}}}}
    action = mcp.McpToolAction(_server({"create_task": "write"}), tool)
    made = action.prepare({"request_words": "create a task", "project_id": "P-7", "title": "Fix login",
                           "state": "Todo"}, _ctx("create a task in P-7: fix login"))
    assert "state" not in made.payload["args"] and ["Left out", "state (not in your request)"] in made.payload["details"]


def test_an_optional_field_in_a_shape_the_tool_does_not_take_is_left_out():
    """Notion refused a page whose optional ``parent`` the model filled in
    wrong, so the whole card failed. It is left out and said instead."""
    tool = {"name": "create_pages", "inputSchema": {"type": "object", "required": ["pages"], "properties": {
        "pages": {"type": "array"},
        "parent": {"anyOf": [{"type": "object", "required": ["page_id"], "properties": {"page_id": {"type": "string"}}},
                             {"type": "object", "required": ["type"], "properties": {"type": {"enum": ["workspace"]}}}]}}}}
    action = mcp.McpToolAction(_server(), tool)
    made = action.prepare({"request_words": "make a page", "pages": [{"content": "- one"}], "parent": {"type": "memory"}},
                          _ctx("make a page in memory"))
    assert "parent" not in made.payload["args"]
    assert ["Left out", "parent (not in a form Asana takes)"] in made.payload["details"]
    # The model is told the forms it may use (it sent Notion a bare page id).
    assert mcp.form_problem(tool["inputSchema"]["properties"]["parent"], "abc123", "parent") == \
        'parent must be one of: {"page_id": …}, {"type": …}'


NOTION_PAGES = {"name": "create_pages", "inputSchema": {"type": "object", "required": ["pages"], "properties": {
    "pages": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
        "properties": {"type": "object"}, "content": {"type": "string"}}}}}}}


def test_a_form_the_tools_schema_refuses_is_caught_before_the_card():
    """Notion refused a page with "title" where its schema has none, after the
    person had approved it. The schema is checked before the card instead."""
    bad = {"pages": [{"title": "Notes", "content": "- one"}]}
    assert "has no field title" in mcp.form_problem(NOTION_PAGES["inputSchema"], bad)
    assert mcp.form_problem(NOTION_PAGES["inputSchema"], {"pages": [{"properties": {"title": "Notes"}}]}) is None
    assert mcp.form_problem({"type": "integer"}, True) == "the form must be integer"
    assert mcp.form_problem({"anyOf": [{"type": "string"}, {"type": "array"}]}, ["x"]) is None


def test_a_cards_link_is_a_page_never_an_api_address():
    """GitHub's create_branch returns only its API address, a 404 in a browser."""
    assert mcp._page_link("https://api.github.com/repos/o/r/git/refs/heads/mcp-suite-1613") == \
        "https://github.com/o/r/tree/mcp-suite-1613"
    assert mcp._page_link("https://api.github.com/repos/o/r/pulls/21") == "https://github.com/o/r/pull/21"
    assert mcp._page_link("https://api.linear.app/graphql") is None
    assert mcp._page_link("https://linear.app/acme/issue/BEN-1.") == "https://linear.app/acme/issue/BEN-1"



def test_a_merge_with_no_link_links_to_the_pull_request():
    assert mcp._github_page({"owner": "o", "repo": "r", "pullNumber": 25}) == "https://github.com/o/r/pull/25"
    assert mcp._github_page({"owner": "o", "repo": "r", "issue_number": 7}) == "https://github.com/o/r/issues/7"
    assert mcp._github_page({"owner": "o"}) is None


# -- The tool loop ---------------------------------------------------------------------------


class Scripted:
    """A model that makes the scripted calls, one turn each, then replies in words."""

    def __init__(self, *turns):
        self.turns, self.seen = iter(turns), []

    def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
        from app.llm.base import ChatResult, ToolCall

        self.seen.append(list(messages))
        turn = next(self.turns, None)
        if turn is None:
            return ChatResult(text="Done.")
        return ChatResult(text=None, tool_calls=[ToolCall(f"c{len(self.seen)}{i}", a.tool["function"]["name"],
                                                          json.dumps(args)) for i, (a, args) in enumerate(turn)])


def _loop(model, question, offered, found=None, looked=None):
    from app.actions import service

    return service._agent_loop(model, question, [a.tool for a in offered], offered, org_id="o",
                               user_id="u", email="a@x.com", found=[] if found is None else found,
                               looked=set() if looked is None else looked, ran={},
                               settings=SimpleNamespace(mcp_max_lookups=8, mcp_timeout_seconds=5))


def _tool_answers(messages):
    return [m["content"] for m in messages if m.get("role") == "tool"]


def test_a_lookups_answer_comes_back_as_a_tool_result_and_its_id_fills_the_form(server, monkeypatch):
    """The lookup found BEN-1, but its answer sat among the documents the
    prompt forbids targets from, and the model asked the user for the id."""
    srv = _server({"create_task": "write", "list_projects": "lookup"})
    write, find = mcp.McpToolAction(srv, CREATE), mcp.McpLookup(srv, FIND)
    monkeypatch.setattr(mcp.McpLookup, "token_for", lambda self, *a, **k: "tok")
    server.result = {"content": [{"type": "text", "text": '{"id": "P-7", "name": "Atlas"}'}]}
    model = Scripted([(find, {})], [(write, {"request_words": "create a task", "project_id": "P-7", "title": "t"})])
    found, looked = [], set()
    run = _loop(model, "create a task in Atlas", [write, find], found, looked)
    assert [a for a, _, _ in run.picked] == [write] and looked == {"Asana"} and "P-7" in found[0]
    second = model.seen[1]
    assert second[1]["tool_calls"][0]["id"] == second[2]["tool_call_id"]
    assert "P-7" in second[2]["content"] and "UNTRUSTED_TOOL_RESULT" in second[2]["content"]
    assert len(model.seen) == 2                    # paused at the draft: answered once decided


def test_a_guessed_filter_value_that_found_nothing_is_dropped_once(monkeypatch):
    """GPT-OSS searched Linear with ``state: "open"``, a state no team has, until
    its lookups ran out. A value a tool answer showed ("BEN") or the schema
    offers is kept; the guess is dropped and the lookup asked again."""
    tool = {**FIND, "inputSchema": {"type": "object", "properties": {
        "team": {"type": "string"}, "state": {"type": "string"}, "order": {"type": "string", "enum": ["new"]}}}}
    find = mcp.McpLookup(_server({"list_projects": "lookup"}), tool)
    monkeypatch.setattr(mcp.McpLookup, "token_for", lambda self, *a, **k: "tok")
    asked = []
    monkeypatch.setattr(mcp.McpLookup, "run", lambda self, args, tok, timeout: asked.append(args) or (
        '{"issues": []}' if "state" in args else '{"issues": [{"id": "BEN-1", "status": "Todo"}]}'))
    model = Scripted([(find, {"team": "BEN", "state": "open", "order": "new"})])
    _loop(model, "sheet of the open issues in BEN", [find], found=['{"teams": [{"key": "BEN"}]}'])
    assert asked == [{"team": "BEN", "state": "open", "order": "new"}, {"team": "BEN", "order": "new"}]
    said = _tool_answers(model.seen[1])[0]
    assert "BEN-1" in said and "state=open" in said


def test_a_lookups_empty_filters_are_not_sent(server):
    """GPT-OSS filled every optional filter with "" or null; Linear refused the
    whole call each time and the model gave up on an issue that existed."""
    tool = {**FIND, "inputSchema": {"type": "object", "properties": {
        "query": {"type": "string"}, "orderBy": {"type": "string"}, "team": {"type": "string"},
        "labels": {"type": "array"}, "priority": {"type": "integer"}}}}
    mcp.McpLookup(_server({"list_projects": "lookup"}), tool).run(
        {"query": "Checkout", "orderBy": "", "team": None, "labels": [], "priority": 0}, "tok", timeout=5)
    assert server.calls[-1][1]["arguments"] == {"query": "Checkout", "priority": 0}


def test_every_action_of_a_turn_is_drafted_once_and_the_loop_pauses_there():
    """Asked "anything else?", the model drafted the same DM twice: a repeat
    is one card. The loop then waits for the person, as any tool loop waits
    for a tool's answer; nothing more is asked of the model until then."""
    write = mcp.McpToolAction(_server(), CREATE)
    form = lambda t: {"request_words": f"a task titled {t}", "project_id": "P-7", "title": t}  # noqa: E731
    model = Scripted([(write, form("t")), (write, form("t")), (write, form("u"))])
    run = _loop(model, "In P-7 make a task titled t and a task titled u", [write])
    assert [a["title"] for _, a, _ in run.picked] == ["t", "u"] and len(model.seen) == 1
    calls = run.messages[-2]["tool_calls"]
    assert [cid for _, _, cid in run.picked] == [calls[0]["id"], calls[2]["id"]]
    assert "Already drafted" in run.messages[-1]["content"]   # only the repeat is answered now


def test_a_refused_form_goes_back_to_the_model_and_its_fix_is_drafted():
    action = mcp.McpToolAction(_server(), NOTION_PAGES)
    model = Scripted([(action, {"request_words": "make a page", "pages": [{"title": "Notes"}]})],
                     [(action, {"request_words": "make a page", "pages": [{"properties": {"title": "Notes"}}]})])
    run = _loop(model, "make a page", [action])
    assert [a["pages"] for _, a, _ in run.picked] == [[{"properties": {"title": "Notes"}}]]
    assert "has no field title" in _tool_answers(model.seen[1])[0]
    assert run.refused == []                       # fixed, so not said to the person


def test_a_value_nobody_gave_is_said_back_so_the_model_looks_it_up(server, monkeypatch):
    srv = _server({"create_task": "write", "list_projects": "lookup"})
    write, find = mcp.McpToolAction(srv, CREATE), mcp.McpLookup(srv, FIND)
    monkeypatch.setattr(mcp.McpLookup, "token_for", lambda self, *a, **k: "tok")
    server.result = {"content": [{"type": "text", "text": '{"id": "P-9", "name": "Atlas"}'}]}
    form = {"request_words": "create a task", "project_id": "P-9", "title": "t"}
    model = Scripted([(write, form)], [(find, {})], [(write, form)])
    run = _loop(model, "create a task in Atlas", [write, find])
    assert "must come from your request" in _tool_answers(model.seen[1])[0]
    assert len(run.picked) == 1 and run.refused == []


def test_a_model_that_only_repeats_itself_is_stopped():
    write = mcp.McpToolAction(_server(), CREATE)
    form = {"request_words": "make a task", "project_id": "P-7", "title": "t"}
    model = Scripted(*([[(write, form)]] * 20))
    run = _loop(model, "make a task in P-7", [write])
    assert len(run.picked) == 1 and len(model.seen) == 1


def test_an_action_nobody_asked_for_is_refused_and_said():
    write = mcp.McpToolAction(_server(), CREATE)
    model = Scripted([(write, {"request_words": "delete everything", "project_id": "P-7", "title": "t"})])
    run = _loop(model, "what tasks are in P-7?", [write])
    assert run.picked == [] and "only act when you ask" in run.refused[0]


def test_a_code_the_fields_description_defines_for_the_askers_words_is_kept():
    """"priority medium" filled Linear's priority with 3 ("3=Medium"), and the
    card left it out because 3 is not in the request."""
    prop = {"type": "number", "description": "0=None, 1=Urgent, 2=High, 3=Medium, 4=Low"}
    assert mcp._code_label("3", prop) == "Medium"
    assert mcp._code_label("3", {"description": "how many"}) is None
    tool = {**CREATE, "inputSchema": {**CREATE["inputSchema"], "properties": {
        **CREATE["inputSchema"]["properties"], "priority": prop}}}
    action = mcp.McpToolAction(_server(), tool)
    asked = action.prepare({"project_id": "P-7", "title": "t", "priority": 3},
                           _ctx("make a task in P-7, priority medium"))
    assert asked.payload["args"]["priority"] == 3


def test_a_code_nobody_asked_for_is_left_out_even_when_a_lookup_holds_it():
    """GPT-OSS added priority 0 to "Create a Linear issue in BEN titled …":
    the team lookup's text held a "0", so it passed as grounded."""
    prop = {"type": "number", "description": "0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low"}
    tool = {**CREATE, "inputSchema": {**CREATE["inputSchema"], "properties": {
        **CREATE["inputSchema"]["properties"], "priority": prop}}}
    action = mcp.McpToolAction(_server(), tool)
    got = action.prepare({"project_id": "P-7", "title": "t", "priority": 0},
                         _ctx("make a task in P-7", lookups='{"id": "P-7", "count": 0}'))
    assert "priority" not in got.payload["args"]


def test_an_id_a_lookup_returned_is_shown_by_its_name():
    action = mcp.McpToolAction(_server(), CREATE)
    team = "db73111d-8073-4456-8cdb-b184d67b5318"
    got = action.prepare({"project_id": team, "title": "t"},
                         _ctx("make a task in BEN", lookups=f'[{{"id": "{team}", "name": "Bench", "key": "BEN"}}]'))
    assert got.payload["args"]["project_id"] == team
    assert dict(got.payload["details"])["project_id"] == "Bench (BEN)"


def test_the_askers_dashes_match_the_models():
    """The asker typed "UI test – slack bug" (en dash); the draft's plain hyphen
    was not their words, so the card asked for the private-content tick."""
    from app.actions.service import _only_askers_words

    assert _only_askers_words({"title": "UI test - slack bug"}, 'Create an issue titled "UI test \u2013 slack bug"')


def test_a_models_non_breaking_hyphens_are_the_askers_plain_ones():
    """GPT-OSS quoted "sana18‑syv/bench‑sandbox" (U+2011): the GitHub half of a
    two-tool request was refused as not asked for."""
    write = mcp.McpToolAction(_server(), CREATE)
    model = Scripted([(write, {"request_words": "a task in P\u20117", "project_id": "P\u20117", "title": "t"})])
    run = _loop(model, "make a task in P-7", [write])
    assert [a["project_id"] for _, a, _ in run.picked] == ["P-7"]


def test_a_model_that_says_it_drafted_without_calling_the_tool_is_told_once(server, monkeypatch):
    """GPT-OSS looked the issue up, then replied "I've drafted a change" and
    called nothing: no card, and the person was told it was done."""
    from app.llm.base import ChatResult

    srv = _server({"create_task": "write", "list_projects": "lookup"})
    write, find = mcp.McpToolAction(srv, CREATE), mcp.McpLookup(srv, FIND)
    monkeypatch.setattr(mcp.McpLookup, "token_for", lambda self, *a, **k: "tok")
    server.result = {"content": [{"type": "text", "text": '{"id": "P-7"}'}]}

    class Claims(Scripted):
        def generate_with_tools(self, messages, **kw):
            if len(self.seen) == 1 and messages[-1]["role"] == "tool":
                self.seen.append(list(messages))
                return ChatResult(text="I've drafted the task.")       # but called nothing
            return super().generate_with_tools(messages, **kw)

    model = Claims([(find, {})], [(write, {"request_words": "create a task", "project_id": "P-7", "title": "t"})])
    run = _loop(model, "create a task in Atlas", [write, find])
    assert model.seen[2][-1]["content"] == act_prompts.NOTHING_DRAFTED and len(run.picked) == 1


def test_an_ask_shared_with_another_action_is_the_askers_words_in_order():
    """"Create a Linear issue in BEN and a GitHub issue in acme/app": the GitHub
    ask is "Create … a GitHub issue in acme/app", in no one phrase."""
    from app.actions.draft import asked_for

    q = "Create a Linear issue in BEN and a GitHub issue in acme/app, both titled x"
    assert asked_for("Create a GitHub issue in acme/app", q)
    assert asked_for('Create two Linear issues in BEN: "B"', 'Create two Linear issues in BEN: "A" and "B".')
    assert not asked_for("Delete a GitHub issue", q)
    assert not asked_for("GitHub issue Create", q)            # out of order
    assert not asked_for("please file", "what is blocking Platform?") and not asked_for("", q)


def test_a_refused_form_brings_the_tools_whole_manual_and_each_new_try_is_progress():
    """Notion's "set the title in properties" sat past the 300 characters a
    card shows, so the model never learned where the title goes; and two
    refusals in a row ended the loop while it was still fixing the form."""
    long = {**NOTION_PAGES, "description": "x" * 400 + " Set the title in properties."}
    action = mcp.McpToolAction(_server(), long)
    tries = [(action, {"request_words": "make a page", "pages": [{"title": f"Notes {i}"}]}) for i in range(3)]
    model = Scripted([tries[0]], [tries[1]], [tries[2]],
                     [(action, {"request_words": "make a page", "pages": [{"properties": {"title": "Notes"}}]})])
    run = _loop(model, "make a page", [action])
    told = _tool_answers(model.seen[1])[0]
    assert "Set the title in properties" in told and "UNTRUSTED_TOOL_RESULT" in told
    assert len(run.picked) == 1                    # three different wrong forms, then the right one


def test_company_content_is_read_through_its_tool_and_only_for_the_askers_words():
    """"… summarising the Q4 roadmap page" made a page that said "Summarising
    the Q4 roadmap page.": a field the model had to fill was left empty, so
    nothing was read. Reading is a tool now, as Onyx's search is."""
    from app.actions import service
    from app.actions.base import Source

    write = mcp.McpToolAction(_server(), CREATE)
    reader = SimpleNamespace(tool=act_prompts.SEARCH)
    asked = []

    def read(words, **kw):
        asked.append(words)
        return [Source("d1", "Q4 roadmap", False, ("a@x.com",))], "[Q4 roadmap]\nShip search in Q4."

    q = "make a task in P-7 summarising the Q4 roadmap page"
    model = Scripted([(reader, {"content_words": "the board minutes"})],
                     [(reader, {"content_words": "the Q4 roadmap page"})],
                     [(write, {"request_words": "make a task", "project_id": "P-7", "title": "Q4",
                               "notes": "Ship search in Q4."})])
    run = service._agent_loop(model, q, [write.tool], [write], org_id="o", user_id="u", email="a@x.com",
                              found=[], looked=set(), ran={},
                              settings=SimpleNamespace(mcp_max_lookups=8, mcp_timeout_seconds=5), read=read)
    assert asked == ["the Q4 roadmap page"]                      # the unasked read never ran
    assert "QUESTION's own words" in _tool_answers(model.seen[1])[0]
    assert "Ship search in Q4" in _tool_answers(model.seen[2])[-1]
    assert [s.document_id for s in run.sources] == ["d1"] and len(run.picked) == 1


def test_a_tool_call_the_model_wrote_as_text_is_made():
    """GPT-OSS replied with the call instead of making it, by name or as the
    bare arguments of the one tool they fit; nothing was drafted."""

    write = mcp.McpToolAction(_server(), CREATE)
    tools = [write.tool]
    name = write.tool["function"]["name"]
    named = act_loop._calls_in_text(f'Here: {{"tool": "{name}", "toolInput": {{"project_id": "P-7", "title": "t"}}}}',
                                   tools)
    assert [(c.name, json.loads(c.arguments)) for c in named] == [(name, {"project_id": "P-7", "title": "t"})]
    bare = act_loop._calls_in_text('{"project_id": "P-7", "title": "t", "request_words": "make"}', tools)
    assert [c.name for c in bare] == [name]
    assert act_loop._calls_in_text('{"colour": "red"}', tools) == []           # fits no form
    assert act_loop._calls_in_text("Done, nothing to add.", tools) == []


def test_an_optional_choice_nobody_asked_for_is_left_out():
    """GPT-OSS set Notion's creation_mode to "draft", which Notion refuses
    beside a parent, but only after the person approved the card."""
    schema = {"type": "object", "required": ["parent"], "properties": {
        "parent": {"type": "string"}, "title": {"type": "string"},
        "creation_mode": {"type": "string", "enum": ["draft", "page"]},
        "state": {"type": "string", "enum": ["open", "closed"]}}}
    action = mcp.McpToolAction(_server(), {"name": "create_pages", "description": "Pages.", "inputSchema": schema})
    got = action.prepare({"parent": "P-7", "title": "Notes", "creation_mode": "draft"}, _ctx("make a page in P-7"))
    assert "creation_mode" not in got.payload["args"]
    assert "creation_mode (not in your request)" in dict(got.payload["details"])["Left out"]
    closed = action.prepare({"parent": "P-7", "state": "closed"}, _ctx("close the page in P-7"))
    reopened = action.prepare({"parent": "P-7", "state": "open"}, _ctx("reopen the page in P-7"))
    assert closed.payload["args"]["state"] == "closed" and reopened.payload["args"]["state"] == "open"


def test_an_empty_list_is_not_sent_as_a_value():
    """Closing an issue, GPT-OSS sent ``labels: []``: on an update GitHub
    reads that as "remove every label"."""
    schema = {"type": "object", "required": ["parent"], "properties": {
        "parent": {"type": "string"}, "labels": {"type": "array", "items": {"type": "string"}},
        "meta": {"type": "object"}}}
    action = mcp.McpToolAction(_server(), {"name": "update_page", "description": "Pages.", "inputSchema": schema})
    got = action.prepare({"parent": "P-7", "labels": [], "meta": {}}, _ctx("update P-7"))
    assert got.payload["args"] == {"parent": "P-7"}


def test_a_card_missing_part_of_the_request_is_sent_back_once_then_said():
    """"DM Raj the #hb-testing pointers and the link of the issue" became a DM
    with neither: whatever the model, a half-done card is not shown as done."""
    from app.actions import service

    class Judge:
        def __init__(self, covers):
            self.covers, self.prompts = list(covers), []

        def generate(self, prompt, max_tokens=None):
            self.prompts.append(prompt)
            if "Split this request into its parts" in prompt:
                return json.dumps([{"quote": "make a task", "kind": "action"}, {"quote": "P-7", "kind": "target"},
                                   {"quote": "the roadmap summary", "kind": "content"},
                                   {"quote": "a part nobody wrote", "kind": "content"}])
            return self.covers.pop(0)

    write = mcp.McpToolAction(_server(), CREATE)
    form = {"request_words": "make a task", "project_id": "P-7", "title": "t"}
    q = "make a task in P-7 with the roadmap summary"
    judge = Judge(["[1]", "[1]"])          # only the content parts are judged
    check = service._completeness(judge, q)
    model = Scripted([(write, form)], [(write, {**form, "title": "t2"})])
    run = service._agent_loop(model, q, [write.tool], [write], org_id="o", user_id="u", email="a@x.com",
                              found=[], looked=set(), ran={},
                              settings=SimpleNamespace(mcp_max_lookups=8, mcp_timeout_seconds=5), check=check)
    assert "does not yet include what the QUESTION asks of it: the roadmap summary" in _tool_answers(model.seen[1])[0]
    assert len(run.picked) == 1 and run.gaps[write.name] == ["the roadmap summary"]   # said, not hidden
    assert "a part nobody wrote" not in judge.prompts[1]                         # parts are the asker's words
    assert service._completeness(object(), q)("x", form) == []                   # a failed check never blocks
    # request_words quoting the whole request ("make a task in P-7 with the
    # roadmap summary") does not make the summary part of the card itself.
    whole = Judge(["[1]"])
    assert service._completeness(whole, q)("x", {**form, "request_words": q}) == ["the roadmap summary"]
    # Judged on the body only: a title naming the summary is not the summary.
    titled = Judge(["[1]"])
    service._completeness(titled, q)("x", {**form, "title": "Summary of the plan", "body": "b"})
    assert "Summary of the plan" not in titled.prompts[-1] and '"body": "b"' in titled.prompts[-1]


def test_a_card_drafted_from_a_named_channel_it_never_read_is_sent_back():
    """GPT-OSS commented "pointers from #hb-testing today" it made up without
    reading the channel: text that reads as complete to any check of it."""
    from app.actions import service

    write = mcp.McpToolAction(_server(), CREATE)
    search = SimpleNamespace(tool=act_prompts.SEARCH)
    q = "make a task in P-7 with what #hb-testing discussed today"
    form = {"request_words": "make a task", "project_id": "P-7", "title": "t"}
    model = Scripted([(write, form)],                                       # drafted before reading
                     [(search, {"content_words": "#hb-testing", "slack_channel": "hb#hb-testing"})],  # garbled
                     [(write, {**form, "title": "t3"})])
    asked = []
    run = service._agent_loop(model, q, [write.tool], [write], org_id="o", user_id="u", email="a@x.com",
                              found=[], looked=set(), ran={}, read=lambda w, **kw: asked.append(kw) or ([], "x"),
                              settings=SimpleNamespace(mcp_max_lookups=8, mcp_timeout_seconds=5))
    assert 'Read #hb-testing first' in _tool_answers(model.seen[1])[0]
    assert asked == [{"channel": "hb-testing", "days": None}]
    assert [a["title"] for _, a, _ in run.picked] == ["t3"] and not run.gaps


def test_a_reply_in_words_keeps_only_the_links_the_model_was_shown():
    """Asked to post an issue's link, the model answered in words and the
    link it was given became "[link removed]"; one it made up still goes."""
    from app.llm.base import ChatResult

    class Says:
        def generate_with_tools(self, messages, **kw):
            return ChatResult(text="Here: https://linear.app/x/issue/BEN-5 and https://evil.example/a")

    write = mcp.McpToolAction(_server(), CREATE)
    run = _loop(Says(), "post the link of the issue we created", [write],
                found=["- Linear: issue, id BEN-5, link https://linear.app/x/issue/BEN-5"])
    assert run.reply == "Here: https://linear.app/x/issue/BEN-5 and [link removed]"


SHEET = {"name": "create_sheet", "title": "Create sheet", "description": "A new sheet.",
         "inputSchema": {"type": "object", "required": ["title"], "properties": {
             "title": {"type": "string"},
             "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}}}


def test_content_that_comes_from_a_lookup_is_looked_up_before_it_is_drafted(server, monkeypatch):
    """"A Google Sheet … with the title, assignee and status of the open issues
    in BEN" became an empty sheet: no lookup ran, and the judge passed it."""
    from app.actions import service

    q = 'Create a sheet titled "BEN open issues" with the title and status of the open issues in BEN'

    class Judge:
        def generate(self, prompt, max_tokens=None):
            if "Split this request into its parts" in prompt:
                return json.dumps([{"quote": "the title and status of the open issues in BEN", "kind": "content",
                                    "in": 'a sheet titled "BEN open issues"', "values": [], "from": "lookup"}])
            return "[]"                                   # the judge sees nothing missing

    srv = _server({"list_projects": "lookup"})
    sheet, find = mcp.McpToolAction(srv, SHEET), mcp.McpLookup(srv, FIND)
    monkeypatch.setattr(mcp.McpLookup, "token_for", lambda self, *a, **k: "tok")
    server.result = {"content": [{"type": "text", "text": json.dumps({"issues": [
        {"title": "Login fails", "status": "Todo"}, {"title": "Export slow", "status": "Done"}]})}]}
    form = {"request_words": "Create a sheet", "title": "BEN open issues"}
    rows = [["Title", "Status"], ["Login fails", "Todo"], ["Export slow", "Done"]]
    model = Scripted([(sheet, form)], [(find, {})], [(sheet, {**form, "rows": rows})])
    run = service._agent_loop(model, q, [sheet.tool, find.tool], [sheet, find], org_id="o", user_id="u",
                              email="a@x.com", found=[], looked=set(), ran={},
                              settings=SimpleNamespace(mcp_max_lookups=8, mcp_timeout_seconds=5),
                              check=service._completeness(Judge(), q))
    assert "Nothing has been read or looked up yet" in _tool_answers(model.seen[1])[0]
    assert [a.get("rows") for _, a, _ in run.picked] == [rows] and not run.gaps


def test_a_header_alone_is_not_content_that_comes_from_a_lookup():
    """Prod: a sheet of just "Title, Assignee, Status" passed, because the
    parts call listed those column names as the content's items."""
    from app.actions import service

    q = 'Create a Google Sheet titled "BEN open issues" with the title, assignee and status of the open issues in BEN'
    part = "the title, assignee and status of the open issues in BEN"

    class Judge:                                   # GPT-OSS's own parts for this request
        def generate(self, prompt, max_tokens=None):
            if "Split this request into its parts" in prompt:
                return json.dumps([{"quote": part, "kind": "content", "in": 'Google Sheet titled "BEN open issues"',
                                    "values": ["title", "assignee", "status"], "from": "lookup"}])
            return "[1]"                           # GPT-OSS judged every BEN issue as missing

    check = service._completeness(Judge(), q)
    label = "Google Workspace: Create a Google Sheet"
    header = [["Title", "Assignee", "Status"]]
    assert check(label, {"title": "BEN open issues", "rows": header}) == [part]
    filled = header + [["Login fails", "Asha", "Todo"], ["Export slow", "Ravi", "Done"]]
    assert check(label, {"title": "BEN open issues", "rows": filled}) == []


def test_a_cards_list_items_must_come_from_what_was_fetched():
    from app.actions.checks import _untraced

    fetched = ['{"issues": [{"title": "Login fails", "status": "Todo"}, {"title": "Export slow", "status": "Done"}]}']
    made_up = {"rows": [["Title", "Status"], ["Checkout bug", "Open"], ["Search slow", "Open"]]}
    real = {"rows": [["Title", "Status"], ["Login fails", "Todo"], ["Export slow", "Done"]]}
    redated = {"rows": [["Login fails", "Todo", "Oct 21"], ["Export slow", "Done", "Oct 22"]]}
    assert _untraced(made_up, ["the title and status", *fetched])
    assert not _untraced(real, ["the title and status", *fetched])
    assert not _untraced(redated, fetched)                      # a reformatted date is one cell of many
    assert not _untraced({"labels": ["bug"]}, [])               # one label is not a table
    # Prod: one made-up issue under the request's own header was "half traced".
    invented = {"rows": [["Title", "Assignee", "Status"], ["Weekly report email has broken formatting", "Sana Asiwal", "Backlog"]]}
    q = "a sheet with the title, assignee and status of the open issues in BEN"
    assert _untraced(invented, [q, '{"issues": []}'], q)
    assert not _untraced(real, [q, *fetched], q)


def test_a_list_the_model_ended_with_an_ellipsis_is_cut_short():
    """Prod: a sheet of BEN's open issues stopped after three rows at "E3..."."""
    from app.actions.checks import _cut_short

    fetched = ['{"issues": [{"title": "Final E2E"}, {"title": "E3E Three"}, {"title": "Waiting..."}]}']
    cut = {"rows": [["Title"], ["Final E2E"], ["E3..."]]}
    assert _cut_short(cut, fetched) == ["e3..."]
    assert _cut_short({"rows": [["Final E2E"], ["Waiting..."]]}, fetched) == []   # the source's own words


def test_a_lookup_that_found_nothing_asks_for_another_search():

    assert act_checks._found_nothing('[Linear: List issues] {"issues": []}')
    assert act_checks._found_nothing('{"total_count": 0, "items": []}') and act_checks._found_nothing("[]")
    assert not act_checks._found_nothing('{"issues": [{"id": "BEN-1"}]}')
    assert not act_checks._found_nothing("No issues matched.")                       # words are never judged
    cut = '[Linear: List issues]\n{"issues":[{"id":"BEN-173","labels":[],"title":"Agent ch'   # cut at its bound
    assert not act_checks._found_nothing(cut)


def test_a_records_own_address_from_a_lookup_may_go_in_a_draft():
    """BEN-161 was found by a lookup; its link was dropped as "nobody gave it".
    A link written inside a description it returned still may not."""
    action = mcp.McpToolAction(_server(), CREATE)
    lookups = ('{"id": "BEN-161", "url": "https://linear.app/x/issue/BEN-161", '
               '"description": "see https://evil.example/steal"}')
    got = action.prepare({"project_id": "P-7", "title": "t",
                          "notes": "Issue: https://linear.app/x/issue/BEN-161 and https://evil.example/steal"},
                         _ctx("make a task in P-7", lookups=lookups))
    assert "https://linear.app/x/issue/BEN-161" in got.payload["body"] and "evil.example" not in got.payload["body"]


def test_the_same_card_in_other_words_is_not_drafted_again(monkeypatch):
    """GPT-OSS left GitHub's owner out, then after approval wrote owner "bench";
    both were filled as the same repository, and the issue was drafted twice."""
    from app.actions import service

    write = mcp.McpToolAction(_server(), CREATE)
    form = {"request_words": "make a task", "project_id": "P-7", "title": "t"}
    monkeypatch.setattr(write, "prepare", lambda args, ctx: SimpleNamespace(
        payload={"args": {"project_id": "P-7", "title": "t"}}))      # what Handbook fills in
    first = _loop(Scripted([(write, form)]), "make a task in P-7 titled t", [write])
    model = Scripted([(write, {**form, "owner": "guessed"})])
    again = service._agent_loop(model, "make a task in P-7 titled t", [write.tool], [write], org_id="o",
                                user_id="u", email="a@x.com", found=[], looked=set(), ran={},
                                already=frozenset(first.keys),
                                settings=SimpleNamespace(mcp_max_lookups=8, mcp_timeout_seconds=5))
    assert len(first.picked) == 1 and not again.picked
    assert "already decided" in _tool_answers(model.seen[1])[0]


def test_a_channel_read_for_zero_days_reads_today_and_says_it_is_the_whole_channel():
    """"days": "0" read a week, and GPT-OSS found "no pointers about the checkout
    timeout" in the channel it had read: it is every message, not matches."""
    from app.actions import service

    search = SimpleNamespace(tool=act_prompts.SEARCH)
    model = Scripted([(search, {"content_words": "#hb-testing today", "slack_channel": "hb-testing", "days": "0"})])
    asked = []
    service._agent_loop(model, "summarise #hb-testing today", [], [], org_id="o", user_id="u", email="a@x.com",
                        found=[], looked=set(), ran={}, read=lambda w, **kw: asked.append(kw) or ([], "x"),
                        settings=SimpleNamespace(mcp_max_lookups=8, mcp_timeout_seconds=5))
    assert asked == [{"channel": "hb-testing", "days": 1}]
    assert "every message in #hb-testing" in _tool_answers(model.seen[1])[0]


def test_a_write_with_no_link_of_its_own_links_to_the_record_it_acted_on():
    """A Linear comment answers with ids only; its card had nothing to open."""
    looked = ('[Linear: List issues]\n{"issues": [{"id": "BEN-168", "priority": {"value": 0}, '
              '"url": "https://linear.app/acme/issue/BEN-168/e2e"}]}')
    assert mcp._target_link({"issueId": "BEN-168", "body": "Verified"}, looked) == \
        "https://linear.app/acme/issue/BEN-168/e2e"
    earlier = '- Linear: Save issue: "E2E", id BEN-170, link https://linear.app/acme/issue/BEN-170/x'
    assert mcp._target_link({"issueId": "BEN-170"}, earlier) == "https://linear.app/acme/issue/BEN-170/x"
    assert mcp._target_link({"issueId": "BEN-9"}, looked) is None                 # no record of it
    assert mcp._target_link({"body": "BEN-168"}, looked) is None                  # drafted text is not a target


def test_a_channel_the_request_only_posts_to_is_not_read_first():
    """"…then post both links in #hb-testing": reading it put a week of the
    channel into the Linear issue. Only a channel named as content is read."""
    from app.actions import service

    write = mcp.McpToolAction(_server(), CREATE)
    form = {"request_words": "make a task", "project_id": "P-7", "title": "t"}
    q = "make a task in P-7 titled t, then post its link in #hb-testing"

    def check(label, args):
        return []
    check.content = lambda: ["its link"]                 # the parts check: #hb-testing is no content
    model = Scripted([(write, form)])
    run = service._agent_loop(model, q, [write.tool], [write], org_id="o", user_id="u", email="a@x.com",
                              found=[], looked=set(), ran={}, read=lambda w, **kw: ([], "x"), check=check,
                              settings=SimpleNamespace(mcp_max_lookups=8, mcp_timeout_seconds=5))
    assert len(run.picked) == 1 and not run.gaps
    check.content = lambda: ["what #hb-testing discussed"]
    model = Scripted([(write, form)])
    run = service._agent_loop(model, q, [write.tool], [write], org_id="o", user_id="u", email="a@x.com",
                              found=[], looked=set(), ran={}, read=lambda w, **kw: ([], "x"), check=check,
                              settings=SimpleNamespace(mcp_max_lookups=8, mcp_timeout_seconds=5))
    assert "Read #hb-testing first" in _tool_answers(model.seen[1])[0]


def test_an_approved_action_is_not_done_again_in_other_text():
    """After "Post in #hb-testing the pointers…" was approved, GPT-OSS posted
    the issue's link there too, quoting the same words."""
    from app.actions import service

    write = mcp.McpToolAction(_server(), CREATE)
    form = {"request_words": "make a task", "project_id": "P-7", "title": "t"}
    first = _loop(Scripted([(write, form)]), "make a task in P-7", [write])
    model = Scripted([(write, {**form, "title": "something else"})],
                     [(write, {**form, "request_words": "make a task in P-7", "title": "x"})])
    again = service._agent_loop(model, "make a task in P-7", [write.tool], [write], org_id="o", user_id="u",
                                email="a@x.com", found=[], looked=set(), ran={},
                                already=frozenset(first.did.values()),
                                settings=SimpleNamespace(mcp_max_lookups=8, mcp_timeout_seconds=5))
    assert "already did this part" in _tool_answers(model.seen[1])[0]
    assert len(again.picked) == 1            # other words asking for it: another part of the request


def test_content_for_another_card_or_already_on_this_one_is_not_judged():
    """GPT-OSS judged a sheet holding every column and row as lacking them, and
    asked the Slack post for them, which then repeated the sheet."""
    from app.actions import service

    q = 'Create a Google Sheet titled "T" with columns Issue, Tool, then post in #eng that the tracker is ready'

    class Judge:
        judged = []

        def generate(self, prompt, max_tokens=None):
            if "Split this request into its parts" in prompt:
                return json.dumps([
                    {"quote": "columns Issue, Tool", "kind": "content", "in": 'a Google Sheet titled "T"',
                     "values": ["Issue", "Tool"]},
                    {"quote": "that the tracker is ready", "kind": "content", "in": "post in #eng", "values": []}])
            self.judged.append(prompt)
            return "[1]"

    judge = Judge()
    check = service._completeness(judge, q)
    sheet = {"title": "T", "rows": [["Issue", "Tool"]]}
    assert check("Google Workspace: Create a Google Sheet", sheet) == []        # its items are all there
    assert judge.judged == []                                                   # and the post's part is not its own
    assert check("Slack: Send a message", {"channel": "#eng", "text": "Done"}) == ["that the tracker is ready"]
    listed = judge.judged[-1].split("It asks for this content:")[1].split("A card was drafted")[0]
    assert listed.strip() == "1. that the tracker is ready"                   # the sheet's part is not the post's
    # The items quoted in request_words are not on the card: an empty sheet
    # for "…with columns Issue, Tool…" passed as holding them.
    empty = {"title": "T", "request_words": "Create a Google Sheet titled T with columns Issue, Tool"}
    assert check("Google Workspace: Create a Google Sheet", empty) == ["columns Issue, Tool"]


def test_the_reminder_names_a_card_refused_and_never_drafted_again():
    """A Slack post refused for a link that did not exist yet was never drafted
    again once it did; GPT-OSS said "links posted"."""
    post = SimpleNamespace(label="Slack: Send a message", lookup=False)
    issue = SimpleNamespace(label="GitHub: Create issue", lookup=False)
    by_name = {"post": post, "issue": issue}
    messages = [{"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "post"}}]},
                {"role": "tool", "tool_call_id": "a", "content": "Refused, not drafted: the link …"},
                {"role": "assistant", "tool_calls": [{"id": "b", "function": {"name": "issue"}}]},
                {"role": "tool", "tool_call_id": "b", "content": "Done: the user approved it"}]
    assert "Slack: Send a message" in act_loop._still_refused(messages, by_name)
    messages += [{"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "post"}}]},
                 {"role": "tool", "tool_call_id": "c", "content": "Done: the user approved it"}]
    assert act_loop._still_refused(messages, by_name) == ""


def test_a_call_written_as_text_is_read_whatever_key_holds_its_arguments():
    """GPT-OSS wrote {"tool": "…save_issue", "value": {…}}: no card was drafted."""
    write = mcp.McpToolAction(_server(), CREATE)
    tools = [write.tool]
    name = write.tool["function"]["name"]
    said = act_loop._calls_in_text(f'{{"tool": "{name}", "value": {{"project_id": "P-7", "title": "t"}}}} Done.', tools)
    assert [(c.name, json.loads(c.arguments)) for c in said] == [(name, {"project_id": "P-7", "title": "t"})]
    two = act_loop._calls_in_text(f'{{"tool": "{name}", "a": {{"x": 1}}, "b": {{"y": 2}}}}', tools)
    assert two == []                                   # two objects: which one is not guessed


def test_a_card_needing_what_another_card_of_its_turn_makes_waits_for_it():
    """GPT-OSS drafted a Sheet "in the folder Final E2E" before the card making
    that folder, in one turn; the Sheet ran first and found no folder."""
    from app.actions import service

    folder_tool = {"name": "create_folder", "description": "A folder", "inputSchema": {
        "type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}}
    sheet_tool = {"name": "create_sheet", "description": "A sheet", "inputSchema": {
        "type": "object", "required": ["title"], "properties": {"title": {"type": "string"},
                                                                "folder": {"type": "string"}}}}
    server = _server()
    folder, sheet = mcp.McpToolAction(server, folder_tool), mcp.McpToolAction(server, sheet_tool)
    q = 'Create a folder "Q4" and a sheet "Plan" in the folder "Q4"'
    model = Scripted([(sheet, {"request_words": "a sheet", "title": "Plan", "folder": "Q4"}),
                      (folder, {"request_words": "Create a folder", "name": "Q4"})])
    run = service._agent_loop(model, q, [folder.tool, sheet.tool], [folder, sheet], org_id="o", user_id="u",
                              email="a@x.com", found=[], looked=set(), ran={},
                              settings=SimpleNamespace(mcp_max_lookups=8, mcp_timeout_seconds=5))
    assert [a.mcp_tool["name"] for a, _, _ in run.picked] == ["create_folder"]
    assert any("made by another card" in t for t in _tool_answers(run.messages))


def test_a_link_the_tool_only_echoed_is_not_the_cards_link():
    """A Linear comment holding a GitHub link answered with its body, and
    "Open in Linear" opened the GitHub issue. Its id was Handbook's card id."""
    from app.actions import mcp_client

    tool = {"name": "save_comment", "inputSchema": {"type": "object", "properties": {
        "issueId": {"type": "string"}, "body": {"type": "string"}}}}
    action = mcp.McpToolAction(_server(), tool)

    class Session:
        def __init__(self, *a, **k):
            pass

        def open(self):
            return {}

        def call_tool(self, name, args, write):
            return {"content": [{"type": "text", "text": json.dumps(
                {"id": "c-1", "body": args["body"], "issue": {"identifier": "BEN-171"}})}]}

    original = mcp_client.McpSession
    mcp_client.McpSession = Session
    try:
        made = action.execute("card-uuid", {}, {"args": {"issueId": "BEN-171",
                                                        "body": "https://github.com/o/r/issues/168"},
                                               "target_link": "https://linear.app/acme/issue/BEN-171/x"}, "tok")
    finally:
        mcp_client.McpSession = original
    assert made.url == "https://linear.app/acme/issue/BEN-171/x" and made.external_id == "c-1"


def test_the_reminder_names_an_action_of_the_request_no_card_did():
    """"Add … and share it with Raj as an editor": after the text was approved,
    GPT-OSS stopped and the share was never drafted."""
    write = SimpleNamespace(label="Google: Add text", lookup=False)
    by_name = {"append": write}
    check = SimpleNamespace(actions=lambda: ["Add", "share"])
    messages = [{"role": "assistant", "tool_calls": [{"id": "a", "function": {
        "name": "append", "arguments": json.dumps({"request_words": 'Add "Status" to the Google Doc'})}}]}]
    said = act_loop._not_asked_yet(messages, by_name, check)
    assert "“share”" in said and "“Add”" not in said
    assert act_loop._not_asked_yet(messages, by_name, SimpleNamespace(actions=lambda: ["Add"])) == ""
    assert act_loop._not_asked_yet(messages, by_name, None) == ""


def test_an_id_an_earlier_card_made_is_shown_by_its_name():
    """The share card showed FILE 1Eh7ylq3…: the add-text result named no file."""
    said = act_prompts._decided("Google Workspace: Add text to a Doc", "succeeded",
                                {"external_id": "1Eh7ylq3FaV7b08y6cM2SOErk9tz", "name": "Final E2E notes"}, None)
    assert '"Final E2E notes", id 1Eh7ylq3FaV7b08y6cM2SOErk9tz' in said
    assert mcp._named("1Eh7ylq3FaV7b08y6cM2SOErk9tz", said) == "Final E2E notes"
    assert mcp._named("1Eh7ylq3FaV7b08y6cM2SOErk9tzX", said) != "Final E2E notes"   # whole ids only


def test_a_lookup_keeps_a_coded_filter_only_when_the_asker_chose_it():
    """GPT-OSS searched Linear with priority 0 ("0=None") on every call, and a
    team's open issues came back empty."""
    tool = {"inputSchema": {"type": "object", "properties": {
        "priority": {"type": "number", "description": "0=None, 1=Urgent, 2=High, 3=Medium, 4=Low"},
        "limit": {"type": "number"}, "team": {"type": "string"}}}}
    assert mcp.asked_filters(tool, {"priority": 0, "limit": 20, "team": "BEN"}, "the open issues in BEN") == \
        {"limit": 20, "team": "BEN"}
    assert mcp.asked_filters(tool, {"priority": 2}, "the High priority issues in BEN") == {"priority": 2}


def test_a_tables_cells_are_content_not_targets():
    """"Unassigned" written for an issue with no assignee is in no lookup, and
    the whole table was left out of the Sheet as "not in your request"."""
    grid = {"name": "add_rows", "inputSchema": {"type": "object", "required": ["sheet"], "properties": {
        "sheet": {"type": "string"},
        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}}}}
    action = mcp.McpToolAction(_server(), grid)
    q = "add the open issues in BEN to the Sheet Tracker"
    rows = [["Agent check", "Unassigned", "Backlog"], ["see https://evil.example/x", "", ""]]
    args = action.prepare({"request_words": "add", "sheet": "Tracker", "rows": rows},
                          _ctx(q, lookups='{"title": "Agent check", "status": "Backlog"}')).payload["args"]
    assert args["rows"] == [["Agent check", "Unassigned", "Backlog"], ["see [link removed]", "", ""]]
    with pytest.raises(ActionRejected):                       # the target stays the asker's words
        action.prepare({"request_words": "add", "sheet": "Secret sheet", "rows": rows}, _ctx(q))
