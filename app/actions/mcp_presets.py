"""The five connectors' own MCP servers, as presets an admin adds in one click.

Agent mode writes only through MCP (docs/plans/2026-10-22-mcp-only-actions.md).
A preset knows what a bare URL cannot:

* **How people sign in.** Linear and Notion register Handbook themselves
  (dynamic client registration). GitHub and Slack do not: the sign-in uses the
  app Handbook already has there (``client``). Google's servers advertise no
  sign-in at all, so the preset carries Google's endpoints and the one scope
  Act uses (``oauth``): ``drive.file``, files Handbook made or the person picked.
* **Which tools may ever be switched on** (``allow``). A list of what to allow,
  not of words to block: a vendor adding ``pull_request_review_write`` must not
  become something an admin can turn on by accident. GitHub is held to issues,
  labels and comments, and its sign-in goes through the GitHub App, which holds
  Issues: write only, so GitHub itself refuses code, pull requests and merges.
* **Which tools are reads** (``reads``), for servers that do not mark them.
* **That the account is the company's.** Checked after each person signs in
  (``account_check``) or on each write's target (``target_check``), as the
  built-in actions did; company content never lands in a personal workspace.

A server added by URL with no preset keeps the generic rules: every tool off,
destructive names blocked, lookups only for tools the server marks read-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from ..core.exceptions import ActionRejected


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    url: str
    note: str = ""
    #: ``() -> (client_id, client_secret)`` of Handbook's own app there, when
    #: the server cannot register Handbook itself.
    client: Callable[[], tuple[str | None, str | None]] | None = None
    #: Sign-in endpoints and scope, when the server advertises none.
    oauth: dict | None = None
    allow: re.Pattern | None = None
    reads: re.Pattern | None = None
    account_check: Callable[[str, str, str], bool] | None = None  # (org_id, email, token)
    target_check: Callable[[str, dict], None] | None = None       # (org_id, args)
    extra_auth_params: dict = field(default_factory=dict)
    #: Which of the scopes a server advertises to ask for (least privilege);
    #: ``None`` asks for all of them.
    scopes: re.Pattern | None = None

    def pick_scopes(self, offered: list[str]) -> list[str]:
        return [s for s in offered if self.scopes is None or self.scopes.search(s)]

    def allows(self, tool: str) -> bool:
        return self.allow is None or bool(self.allow.search(tool))

    def is_read(self, tool: str) -> bool:
        return bool(self.reads and self.reads.search(tool))


# -- Linear ---------------------------------------------------------------------------


def _linear_account(org_id: str, email: str, token: str) -> bool:
    """The sign-in is to the Linear workspace the company connected (company-wide
    or in any space): ``get_workspace``'s id is the connection's workspace id."""
    from ..auth.credentials import connected_anywhere
    from .mcp_client import McpSession, result_json

    company = {ext for ext, _cfg in connected_anywhere(org_id, "linear") if ext}
    if not company:
        return False
    session = McpSession(LINEAR.url, token, timeout=30)
    session.open()
    return result_json(session.call_tool("get_workspace", {}, write=False)).get("id") in company


LINEAR = Preset(
    key="linear", name="Linear", url="https://mcp.linear.app/mcp",
    # Issues, comments, labels, projects and documents; never deletes (none exist
    # for issues) or label retirement.
    allow=re.compile(r"^(get_|list_|search_|save_issue$|save_comment$|create_issue_label$)"),
    reads=re.compile(r"^(get_|list_|search_)"),
    account_check=_linear_account,
)


# -- Notion ---------------------------------------------------------------------------

NOTION = Preset(
    key="notion", name="Notion", url="https://mcp.notion.com/mcp",
    note="Each person picks which pages Notion shares when they sign in.",
    allow=re.compile(r"(search|fetch|get|create|update|comment)", re.IGNORECASE),
    reads=re.compile(r"(search|fetch|get-|get_|list)", re.IGNORECASE),
)


# -- GitHub ---------------------------------------------------------------------------


def _github_client() -> tuple[str | None, str | None]:
    from ..config.settings import GitHubSettings

    s = GitHubSettings.from_env()
    return s.client_id, s.client_secret


def _github_target(org_id: str, args: dict) -> None:
    """A write's repository must be one of the company's own installations."""
    from ..auth.credentials import connected_anywhere
    from ..core.exceptions import SourceError
    from ..githublive.repos import resolve_repo, scope_from_config

    owner, repo = str(args.get("owner") or ""), str(args.get("repo") or "")
    if not (owner and repo):
        return  # not aimed at a repository (e.g. a search); GitHub's own checks apply
    for _ext, config in connected_anywhere(org_id, "github"):
        try:
            resolve_repo(scope_from_config(config), f"{owner}/{repo}")
            return
        except SourceError:
            continue
    raise ActionRejected(f"{owner}/{repo} isn't a repository your company connected to Handbook.")


GITHUB = Preset(
    key="github", name="GitHub", url="https://api.githubcopilot.com/mcp/",
    note="Signs in through Handbook's GitHub App, which can only write issues.",
    client=_github_client,
    # A GitHub App's sign-in ignores OAuth scopes; its permissions (Issues: write) apply.
    scopes=re.compile(r"^$"),
    # Issues, their labels and comments, and reads. Never code, branches,
    # pull requests, releases or repositories.
    allow=re.compile(r"^(issue_read|issue_write|add_issue_comment|update_issue_comment|"
                     r"sub_issue_write|get_label|list_label|list_issue|search_issues|"
                     r"get_me|get_file_contents|search_code|list_commits|get_commit)"),
    reads=re.compile(r"^(issue_read|get_|list_|search_)"),
    target_check=_github_target,
)


# -- Slack ----------------------------------------------------------------------------


def _slack_client() -> tuple[str | None, str | None]:
    from ..config.settings import SlackSettings

    s = SlackSettings.from_env()
    return s.client_id, s.client_secret


SLACK = Preset(
    key="slack", name="Slack", url="https://mcp.slack.com/mcp",
    note="Slack lets only approved apps use it: your Slack admin must allow Handbook's app.",
    client=_slack_client,
    # Reading and posting messages only: never channel management, files or canvases.
    scopes=re.compile(r"^(channels|groups|im|mpim):(read|history)$|^(users|search)[:.]|^chat:write$"),
    allow=re.compile(r"(search|read|get|list|send_message|schedule_message|draft)", re.IGNORECASE),
    reads=re.compile(r"(search|read|get|list)", re.IGNORECASE),
)


# -- Google ---------------------------------------------------------------------------


def _google_client() -> tuple[str | None, str | None]:
    from ..config.settings import GoogleSettings

    s = GoogleSettings.from_env()
    return s.client_id, s.client_secret


def _google_account(org_id: str, email: str, token: str) -> bool:
    """The Google account is the person's own work address."""
    import httpx

    try:
        info = httpx.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=15,
                         headers={"Authorization": f"Bearer {token}"}).json()
    except (httpx.HTTPError, ValueError):
        return False
    return bool(info.get("email_verified")) and (info.get("email") or "").lower() == email.lower()


_GOOGLE_OAUTH = {
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    # drive.file: only files Handbook created or the person picked, never the
    # rest of their Drive (no restricted scope, no yearly assessment).
    "scope": "openid email https://www.googleapis.com/auth/drive.file",
}

GOOGLE_DRIVE = Preset(
    key="google_drive", name="Google Drive", url="https://drivemcp.googleapis.com/mcp/v1",
    note="Google's MCP servers are in Developer Preview. Handbook reaches only files it made or you picked.",
    client=_google_client, oauth=_GOOGLE_OAUTH,
    extra_auth_params={"access_type": "offline", "prompt": "consent"},
    allow=re.compile(r"^(create_file|get_file_metadata|read_file_content|search_files|list_recent_files)$"),
    reads=re.compile(r"^(get_|read_|search_|list_)"),
    account_check=_google_account,
)

GOOGLE_SHEETS = Preset(
    key="google_sheets", name="Google Sheets", url="https://sheetsmcp.googleapis.com/mcp/v1",
    note="Google's MCP servers are in Developer Preview. Handbook reaches only Sheets it made or you picked.",
    client=_google_client, oauth=_GOOGLE_OAUTH,
    extra_auth_params={"access_type": "offline", "prompt": "consent"},
    # Values only: ``update_formulas`` would let a draft fetch outside data.
    allow=re.compile(r"^(get_values|get_spreadsheet|update_values|insert_dimension)$"),
    reads=re.compile(r"^get_"),
    account_check=_google_account,
)

PRESETS: dict[str, Preset] = {p.key: p for p in (LINEAR, NOTION, GITHUB, SLACK, GOOGLE_DRIVE, GOOGLE_SHEETS)}


def preset_for(url: str) -> Preset | None:
    """The preset a server was added from, by its address."""
    clean = (url or "").rstrip("/")
    return next((p for p in PRESETS.values() if p.url.rstrip("/") == clean), None)
