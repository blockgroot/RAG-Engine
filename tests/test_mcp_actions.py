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
    assert ["project_id", "P-7"] in proposal.payload["details"] and ["priority", "high"] in proposal.payload["details"]
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


def test_a_lookup_runs_with_the_persons_sign_in_and_its_answer_reaches_the_model(server, monkeypatch):
    from app.actions import service
    from app.config.settings import ActionsSettings

    srv = _server({"create_task": "write", "list_projects": "lookup"})
    write, find = mcp.McpToolAction(srv, CREATE), mcp.McpLookup(srv, FIND)
    monkeypatch.setattr(mcp.McpLookup, "token_for", lambda self, *a, **k: "tok")
    prompts = []
    calls = iter([[("list_projects", {})], [("create_task", {"request_words": "create a task",
                                                             "project_id": "P-7", "title": "t"})]])

    class Model:
        def generate_with_tools(self, messages, tools, tool_choice):
            prompts.append(messages[0]["content"])
            return SimpleNamespace(text="", tool_calls=[
                SimpleNamespace(name=next(a for a in (write, find) if a.mcp_tool["name"] == n).tool["function"]["name"],
                                arguments=json.dumps(args)) for n, args in next(calls)])

    server.result = {"content": [{"type": "text", "text": '{"id": "P-7", "name": "Atlas"}'}]}
    found, looked = [], set()
    picked = service._ask_with_lookups(Model(), "create a task in Atlas", None, [write.tool, find.tool],
                                       [write, find], org_id="o", user_id="u", found=found, looked=looked,
                                       settings=ActionsSettings.from_env())
    assert [a for a, _ in picked] == [write] and looked == {"Asana"}
    assert "P-7" in found[0] and "P-7" in prompts[-1]                      # fenced into the next ask
    assert "UNTRUSTED" in prompts[-1]


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
def test_google_workspace_is_one_tile_adding_drive_and_sheets(client, store, org_cleanup, monkeypatch):
    from app.actions import policy
    from app.auth import create_admin, create_session_token

    monkeypatch.setenv("MCP_REDIRECT_URI", "https://hb.example/api/act/mcp/callback")
    org_id = store.create_organization(f"MCP {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"mcp-{uuid.uuid4().hex[:8]}@example.com", org_id)
    cookies = {"session": create_session_token(admin)}
    policy.set_policy(org_id, policy.ACT_KEY, True)

    tiles = {t["key"]: t for t in client.get("/admin/mcp", cookies=cookies).json()["presets"]}
    assert "google_workspace" in tiles and "google_drive" not in tiles and "google_sheets" not in tiles
    assert client.post("/admin/mcp", json={"preset": "google_workspace"}, cookies=cookies).status_code == 200
    after = client.get("/admin/mcp", cookies=cookies).json()
    assert sorted(s["name"] for s in after["servers"]) == ["Google Drive", "Google Sheets"]
    assert {s["group"] for s in after["servers"]} == {"google_workspace"}
    assert next(t for t in after["presets"] if t["key"] == "google_workspace")["added"]
    assert [s["name"] for s in client.get("/act/mcp", cookies=cookies).json()["servers"]] == ["Google Workspace"]


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
