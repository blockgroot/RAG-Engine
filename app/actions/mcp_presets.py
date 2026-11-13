"""The five connectors' MCP servers, as presets an admin adds in one click.

Linear, Notion and GitHub are the vendors' servers. Slack's and Google's hosted
servers are gated, so those are Handbook's own (``app/mcphost``) over the regular
APIs. A preset knows what a bare URL cannot:

* **How people sign in.** Linear and Notion register Handbook dynamically;
  GitHub signs in through the GitHub App (``client``); Slack (a USER token) and
  Google (``drive.file`` + Docs + Sheets) through Handbook's own apps (``oauth``).
* **Which tools are reads** (``reads``), for servers that do not mark them.
  Which tools are ON is the admin's choice; every tool starts off.
* **That the account is the company's**: ``account_check`` after sign-in, or
  ``target_check`` on each write's target.
* **Which servers are one product** (``group``): unused today.

A server added by URL keeps the generic rules: every tool off, lookups only for
tools the server marks read-only.
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
    #: ``(org_id) -> (client_id, client_secret)`` for servers that cannot register
    #: Handbook: the company's own app (``org_apps``), else settings'.
    client: Callable[[str | None], tuple[str | None, str | None]] | None = None
    #: Sign-in endpoints and scope, when the server advertises none.
    oauth: dict | None = None
    reads: re.Pattern | None = None
    account_check: Callable[[str, str, str], bool] | None = None  # (org_id, email, token)
    target_check: Callable[[str, dict], None] | None = None       # (org_id, args)
    #: Required fields ``target_check`` fills from the company's connections, offered
    #: to the model as optional so it drafts instead of asking.
    fills: tuple[str, ...] = ()
    extra_auth_params: dict = field(default_factory=dict)
    #: Which of the scopes a server advertises to ask for (least privilege);
    #: ``None`` asks for all of them.
    scopes: re.Pattern | None = None
    #: Servers of one product, added together and signed in to once.
    group: str | None = None
    #: The Handbook connector this server belongs to (``oauth_connections.provider``):
    #: in a space, its tools are offered only when that connector is connected there.
    source: str | None = None

    def pick_scopes(self, offered: list[str]) -> list[str]:
        return [s for s in offered if self.scopes is None or self.scopes.search(s)]

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
    key="linear", name="Linear", url="https://mcp.linear.app/mcp", source="linear",
    reads=re.compile(r"^(get_|list_|search_)"),
    account_check=_linear_account,
)


# -- Notion ---------------------------------------------------------------------------

NOTION = Preset(
    key="notion", name="Notion", url="https://mcp.notion.com/mcp", source="notion",
    note="Each person picks which pages Notion shares when they sign in.",
    reads=re.compile(r"(search|fetch|get-|get_|list)", re.IGNORECASE),
)


# -- GitHub ---------------------------------------------------------------------------


def _github_client(org_id: str | None = None) -> tuple[str | None, str | None]:
    from ..config.settings import GitHubSettings

    s = GitHubSettings.from_env()
    return s.client_id, s.client_secret


def _github_target(org_id: str, args: dict) -> None:
    """A write's repository must be one of the company's own installations.

    A missing or wrong owner is set to the ONE installation holding that repo
    (``args["owner"]``); two candidates are refused, never picked."""
    from ..auth.credentials import connected_anywhere
    from ..core.exceptions import SourceError
    from ..githublive.repos import resolve_repo, scope_from_config

    owner, repo = str(args.get("owner") or ""), str(args.get("repo") or "")
    if not repo:
        return  # not aimed at a repository (e.g. a search); GitHub's own checks apply
    scopes = [scope_from_config(config) for _ext, config in connected_anywhere(org_id, "github")]
    named = set()
    for scope in scopes:
        if owner:
            try:
                resolve_repo(scope, f"{owner}/{repo}")
                return
            except SourceError:
                pass
        try:
            named.add(resolve_repo(scope, repo))
        except SourceError:
            continue
    if len(named) == 1:
        args["owner"] = next(iter(named)).split("/")[0]
        return
    raise ActionRejected(f"{owner + '/' if owner else ''}{repo} isn't a repository your company connected to Handbook."
                         + (f" It has several named {repo}: {', '.join(sorted(named))}. Say which." if named else ""))


GITHUB = Preset(
    key="github", name="GitHub", url="https://api.githubcopilot.com/mcp/", source="github",
    note="Signs in through Handbook's GitHub App, which can only write issues.",
    client=_github_client,
    # A GitHub App's sign-in ignores OAuth scopes; its permissions (Issues: write) apply.
    scopes=re.compile(r"^$"),
    reads=re.compile(r"^(issue_read|get_|list_|search_)"),
    target_check=_github_target,
    fills=("owner",),
)


# -- Slack ----------------------------------------------------------------------------


def _slack_client(org_id: str | None = None) -> tuple[str | None, str | None]:
    from ..auth.org_apps import slack_settings

    s = slack_settings(org_id)
    return s.client_id, s.client_secret


def _hosted(name: str) -> str:
    """Handbook's own server for a vendor whose hosted MCP is gated (``app/mcphost``)."""
    from ..config.settings import ActionsSettings

    base = ActionsSettings.from_env().mcp_host_url or "https://handbook-mcp.invalid"
    return f"{base.rstrip('/')}/{name}"


def _slack_account(org_id: str, email: str, token: str) -> bool:
    """The sign-in is to the Slack workspace the company connected."""
    import httpx

    from ..auth.credentials import connected_anywhere

    company = {ext for ext, _cfg in connected_anywhere(org_id, "slack") if ext}
    try:
        team = httpx.post("https://slack.com/api/auth.test", timeout=15,
                          headers={"Authorization": f"Bearer {token}"}).json().get("team_id")
    except (httpx.HTTPError, ValueError):
        return False
    return bool(team) and team in company


SLACK = Preset(
    key="slack", name="Slack", url=_hosted("slack"), source="slack",
    note="Posts as the person who approves it, through Handbook's Slack app.",
    client=_slack_client,
    # Slack's user sign-in (a user token, never the bot's).
    oauth={"authorization_endpoint": "https://slack.com/oauth/v2_user/authorize",
           "token_endpoint": "https://slack.com/api/oauth.v2.user.access",
           "scope": "channels:read groups:read channels:history groups:history users:read "
                    "users:read.email chat:write im:write"},
    reads=re.compile(r"^(list_|find_|read_)"),
    account_check=_slack_account,
)


# -- Google ---------------------------------------------------------------------------


def _google_client(org_id: str | None = None) -> tuple[str | None, str | None]:
    from ..auth.org_apps import google_settings

    s = google_settings(org_id)
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


def _google_target(org_id: str, args: dict) -> None:
    """A file is shared only with someone in the company: company content
    never goes to an outside address, as an account must be the company's."""
    person = str(args.get("person") or "").strip()
    if not person:
        return
    from ..auth.users import get_member_by_address

    if get_member_by_address(person, org_id) is None:
        raise ActionRejected(f"{person} isn't in your company on Handbook, so the file can't be shared with them.")


_GOOGLE_OAUTH = {
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    # drive.file: only files Handbook made or the person opened with it. No restricted
    # scope, so no yearly security assessment.
    "scope": "openid email https://www.googleapis.com/auth/drive.file "
             "https://www.googleapis.com/auth/documents https://www.googleapis.com/auth/spreadsheets",
}

GOOGLE_WORKSPACE = Preset(
    key="google_workspace", name="Google Workspace", url=_hosted("google"), source="google",
    note="Docs and Sheets. Handbook reaches only files it made or that you opened with it.",
    client=_google_client, oauth=_GOOGLE_OAUTH,
    extra_auth_params={"access_type": "offline", "prompt": "consent"},
    reads=re.compile(r"^(search_|read_)"),
    account_check=_google_account,
    target_check=_google_target,
)

PRESETS: dict[str, Preset] = {p.key: p for p in (LINEAR, NOTION, GITHUB, SLACK, GOOGLE_WORKSPACE)}


#: How a group is shown and added: one tile, one sign-in. None today.
GROUPS: dict[str, dict] = {}


def in_group(group: str) -> list[Preset]:
    return [p for p in PRESETS.values() if p.group == group]


#: Vendor-hosted addresses older presets used -> their replacement preset;
#: ``mcp_store`` moves such rows in place.
RETIRED: dict[str, str] = {
    "https://mcp.slack.com/mcp": "slack",
    "https://drivemcp.googleapis.com/mcp/v1": "google_workspace",
    "https://sheetsmcp.googleapis.com/mcp/v1": "google_workspace",
}


def preset_for(url: str) -> Preset | None:
    """The preset a server was added from, by its address."""
    clean = (url or "").rstrip("/")
    return next((p for p in PRESETS.values() if p.url.rstrip("/") == clean), None)
