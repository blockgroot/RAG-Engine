"""The connectors' MCP presets: what an admin can ever turn on, how people sign
in where a server cannot register Handbook, and that the account is the
company's. Offline: no server and no database is contacted."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.actions import mcp_client, mcp_presets
from app.actions.mcp_store import McpServer
from app.core.exceptions import ActionRejected

# The real tool lists of Linear's and GitHub's MCP servers (October 2026).
LINEAR_TOOLS = [
    "create_attachment", "create_issue_label", "delete_attachment", "delete_comment", "get_issue",
    "get_team", "get_workspace", "list_comments", "list_issues", "list_teams", "merge_diff",
    "retire_issue_label", "save_comment", "save_issue", "save_project", "share_issue", "update_diff"]
GITHUB_TOOLS = [
    "add_issue_comment", "create_branch", "create_or_update_file", "create_pull_request",
    "create_repository", "delete_file", "fork_repository", "get_file_contents", "get_me", "issue_read",
    "issue_write", "list_issues", "merge_pull_request", "pull_request_read", "pull_request_review_write",
    "push_files", "run_secret_scanning", "search_issues", "sub_issue_write", "update_issue_comment",
    "update_pull_request", "update_pull_request_branch"]


def _server(preset: mcp_presets.Preset, names: list[str]) -> McpServer:
    return McpServer("s1", "org", preset.name, preset.url, "oauth", {}, [{"name": n} for n in names], {}, True)


def test_nothing_is_blocked_the_admin_decides():
    from app.actions.mcp_store import tool_hash

    tool = {"name": "merge_pull_request"}
    server = McpServer("s1", "org", "GitHub", mcp_presets.GITHUB.url, "oauth", {}, [tool],
                       {"merge_pull_request": {"mode": "write", "hash": tool_hash(tool)}}, True)
    assert server.mode("merge_pull_request") == "write"


def test_a_preset_knows_its_reads_where_the_server_does_not_say():
    server = _server(mcp_presets.LINEAR, LINEAR_TOOLS)
    assert server.is_read({"name": "get_issue"}) and not server.is_read({"name": "save_issue"})


def test_drive_and_sheets_are_one_google_workspace():
    assert {p.key for p in mcp_presets.in_group("google_workspace")} == {"google_drive", "google_sheets"}
    assert mcp_presets.GROUPS["google_workspace"]["name"] == "Google Workspace"


def test_google_signs_in_with_handbooks_client_and_drive_file_only():
    from dataclasses import replace

    preset = replace(mcp_presets.GOOGLE_DRIVE, client=lambda: ("gid", "gsecret"))
    oauth = mcp_client.discover(preset.url, "https://hb.example/api/act/mcp/callback", preset=preset)
    assert oauth["client_id"] == "gid" and oauth["client_secret"] is None and oauth["resource"] is None
    assert oauth["scope"].split() == ["openid", "email", "https://www.googleapis.com/auth/drive.file"]


def test_a_preset_client_secret_comes_from_settings_never_stored(monkeypatch):
    preset = mcp_presets.Preset(key="gh", name="GitHub", url="https://example.invalid/mcp",
                                client=lambda: ("cid", "from-settings"))
    monkeypatch.setitem(mcp_presets.PRESETS, "gh", preset)
    oauth = {"authorization_endpoint": "https://github.com/login/oauth/authorize",
             "token_endpoint": "https://github.com/login/oauth/access_token", "client_id": "cid",
             "client_secret": None, "preset": "gh", "resource": "https://example.invalid/mcp", "scope": None}
    sent = {}

    class Reply:
        status_code = 200

        def json(self):
            return {"access_token": "tok"}

    monkeypatch.setattr(mcp_client.httpx, "post", lambda url, data, **kw: sent.update(data) or Reply())
    mcp_client.exchange(oauth, code="c", verifier="v", redirect_uri="https://hb.example/cb")
    assert sent["client_secret"] == "from-settings" and sent["client_id"] == "cid"


def test_googles_authorize_link_asks_for_offline_access_and_no_resource():
    oauth = {**mcp_presets._GOOGLE_OAUTH, "client_id": "gid", "client_secret": None,
             "preset": "google_drive", "resource": None}
    q = parse_qs(urlparse(mcp_client.authorize_url(oauth, state="s", challenge="c",
                                                   redirect_uri="https://hb.example/cb")).query)
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"] and "resource" not in q


def test_a_github_write_must_target_a_company_repository(monkeypatch):
    monkeypatch.setattr("app.auth.credentials.connected_anywhere", lambda org, provider: [
        ("1", {"installation_id": "1", "account_login": "acme", "repository_selection": "selected",
               "repos": [{"full_name": "acme/handbook"}]})])
    mcp_presets.GITHUB.target_check("org", {"owner": "acme", "repo": "handbook", "title": "t"})
    with pytest.raises(ActionRejected, match="isn't a repository your company connected"):
        mcp_presets.GITHUB.target_check("org", {"owner": "someone", "repo": "personal"})


def test_a_linear_sign_in_must_be_the_company_workspace(monkeypatch):
    monkeypatch.setattr("app.auth.credentials.connected_anywhere", lambda org, provider: [("ws-1", {})])

    class Session:
        def __init__(self, *a, **kw):
            pass

        def open(self):
            return {}

        def call_tool(self, name, args, write):
            return {"content": [{"type": "text", "text": '{"id": "%s"}' % workspace}]}

    monkeypatch.setattr(mcp_client, "McpSession", Session)
    workspace = "ws-1"
    assert mcp_presets.LINEAR.account_check("org", "a@x.com", "tok")
    workspace = "personal"
    assert not mcp_presets.LINEAR.account_check("org", "a@x.com", "tok")


def test_presets_are_found_by_address_with_or_without_a_slash():
    assert mcp_presets.preset_for("https://api.githubcopilot.com/mcp") is mcp_presets.GITHUB
    assert mcp_presets.preset_for("https://mcp.example.com/mcp") is None


def test_slack_asks_only_to_read_and_post_and_github_for_no_oauth_scopes():
    offered = ["channels:write", "chat:write", "files:write", "canvases:write", "channels:read",
               "im:history", "users:read.email", "search:read.public", "reactions:write"]
    assert mcp_presets.SLACK.pick_scopes(offered) == [
        "chat:write", "channels:read", "im:history", "users:read.email", "search:read.public"]
    assert mcp_presets.GITHUB.pick_scopes(["repo", "read:org"]) == []
