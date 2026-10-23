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


def test_slack_and_google_are_handbooks_own_servers():
    """Their hosted MCP servers are gated; Handbook serves them over the regular APIs."""
    for preset, name in ((mcp_presets.SLACK, "slack"), (mcp_presets.GOOGLE_WORKSPACE, "google")):
        assert preset.url.endswith(f"/mcp/{name}") and "googleapis" not in preset.url
        assert "mcp.slack.com" not in preset.url


def test_google_signs_in_with_handbooks_client_and_no_restricted_scope():
    from dataclasses import replace

    preset = replace(mcp_presets.GOOGLE_WORKSPACE, client=lambda: ("gid", "gsecret"))
    oauth = mcp_client.discover(preset.url, "https://hb.example/api/act/mcp/callback", preset=preset)
    assert oauth["client_id"] == "gid" and oauth["client_secret"] is None and oauth["resource"] is None
    assert oauth["scope"].split() == ["openid", "email", "https://www.googleapis.com/auth/drive.file",
                                      "https://www.googleapis.com/auth/documents",
                                      "https://www.googleapis.com/auth/spreadsheets"]


def test_slack_signs_in_as_the_person_never_the_bot():
    from dataclasses import replace

    preset = replace(mcp_presets.SLACK, client=lambda: ("sid", "ssecret"))
    oauth = mcp_client.discover(preset.url, "https://hb.example/cb", preset=preset)
    assert oauth["authorization_endpoint"].endswith("/oauth/v2_user/authorize")
    assert "chat:write" in oauth["scope"].split() and "im:write" in oauth["scope"].split()


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
             "preset": "google_workspace", "resource": None}
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


def test_github_asks_for_no_oauth_scopes():
    assert mcp_presets.GITHUB.pick_scopes(["repo", "read:org"]) == []


def test_a_github_repo_named_by_the_asker_gets_the_company_owner(monkeypatch):
    """Asked "in GitHub bench-sandbox", the model guessed owner "syvora"; the
    company's only bench-sandbox is sana18-syv's. Two candidates are refused."""
    import app.auth.credentials as creds
    import app.githublive.repos as repos
    from app.actions.mcp_presets import _github_target
    from app.core.exceptions import ActionRejected, SourceError

    owned = {"sana18-syv": {"bench-sandbox"}, "other": set()}

    def resolve(scope, name):
        owner, repo = name.split("/") if "/" in name else (scope, name)
        if owner == scope and repo in owned[scope]:
            return f"{owner}/{repo}"
        raise SourceError("no")

    monkeypatch.setattr(creds, "connected_anywhere", lambda org, p: [(1, "sana18-syv"), (2, "other")])
    monkeypatch.setattr(repos, "scope_from_config", lambda config: config)
    monkeypatch.setattr(repos, "resolve_repo", resolve)
    args = {"owner": "syvora", "repo": "bench-sandbox"}
    _github_target("o", args)
    assert args["owner"] == "sana18-syv"
    args = {"repo": "bench-sandbox"}               # left out: "in GitHub bench-sandbox"
    _github_target("o", args)
    assert args["owner"] == "sana18-syv"
    owned["other"].add("bench-sandbox")
    with pytest.raises(ActionRejected, match="several named bench-sandbox"):
        _github_target("o", {"owner": "syvora", "repo": "bench-sandbox"})


def test_github_offers_owner_as_optional_and_fills_it(monkeypatch):
    """GPT-OSS asked "who owns bench-sandbox?" rather than draft; the owner is
    Handbook's to fill from the company's installations, never a reason to ask."""
    from app.actions import mcp, mcp_store
    from app.actions.base import ActionContext

    tool = {"name": "issue_write", "description": "Create an issue", "inputSchema": {
        "type": "object", "required": ["method", "owner", "repo"], "properties": {
            "method": {"type": "string", "enum": ["create", "update"]}, "owner": {"type": "string"},
            "repo": {"type": "string"}, "title": {"type": "string"}}}}
    server = mcp_store.McpServer("s1", "o", "GitHub", mcp_presets.GITHUB.url, "oauth", None, [tool],
                                 {"issue_write": {"mode": "write"}}, True)
    action = mcp.McpToolAction(server, tool)
    assert "owner" not in action.tool["function"]["parameters"]["required"]
    args = {"method": "create", "repo": "bench-sandbox", "title": "Agent check"}
    assert action.form_problem(args) is None
    from dataclasses import replace

    def checked(fill):
        monkeypatch.setitem(mcp_presets.PRESETS, "github", replace(mcp_presets.GITHUB, target_check=fill))

    checked(lambda org, a: a.setdefault("owner", "sana18-syv"))
    ctx = ActionContext("o", None, "u", "a@x.com", "Create an issue titled Agent check in GitHub bench-sandbox",
                        "tok", (), None, None, "")
    assert action.prepare(args, ctx).payload["args"]["owner"] == "sana18-syv"
    checked(lambda org, a: None)
    with pytest.raises(ActionRejected, match="needs owner"):
        action.prepare(args, ctx)
