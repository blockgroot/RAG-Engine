"""GitHub actions: open an issue, comment on an issue or pull request.

Both run on the asker's GitHub App user-to-server token, so GitHub attributes
them to the person (with the App's badge) and allows them only where the
person AND the App may write. Two more limits are ours:

* The repo must be in the company's own installation (``resolve_repo`` on the
  org-wide GitHub connection). A person's token can reach other accounts'
  repos; Act in this company writes only where this company connected.
* The App is never given ``Contents: write``, and no action here touches git
  refs, contents or merges, so nothing can push code to any branch.

Audience: a public repo is public, so private sources never go there. A
private repo's readers are not something we can list with the person's token,
so its audience is unknown and the card asks for an acknowledgement.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..auth.credentials import get_connection_config
from ..core.exceptions import (
    ActionError,
    ActionOutcomeUnknown,
    ActionRejected,
    OAuthReauthRequiredError,
    SourceError,
)
from ..githublive.repos import resolve_repo, scope_from_config
from .base import Action, ActionContext, ActionResult, ActionTarget, Audience, Proposal
from .draft import clean_body, clean_title, require_named

API = "https://api.github.com"
_TIMEOUT = 15.0
_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

# "owner/repo#12", "repo#12", or a github.com issue / pull request address.
_REF = re.compile(
    r"^(?:https://github\.com/)?(?P<repo>[\w.-]+(?:/[\w.-]+)?)"
    r"(?:#|/issues/|/pull/)(?P<number>\d{1,9})/?$"
)

_REQUEST_WORDS = {
    "type": "string",
    "description": (
        "The exact words from the user's message that ask for this action, "
        "copied verbatim (for example 'comment on')."
    ),
}


def _call(method: str, path: str, token: str, *, json: dict | None = None) -> dict:
    write = method != "GET"
    try:
        response = httpx.request(
            method, f"{API}{path}", json=json, timeout=_TIMEOUT,
            headers={**_HEADERS, "Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError as exc:
        if write:
            raise ActionOutcomeUnknown("GitHub did not answer.", cause=exc) from exc
        raise ActionError("Could not reach GitHub.", cause=exc) from exc
    if response.status_code == 401:
        raise OAuthReauthRequiredError("Reconnect your GitHub account.")
    if write and response.status_code >= 500:
        raise ActionOutcomeUnknown(f"GitHub answered {response.status_code}.")
    if response.status_code in (403, 404):
        # 404 is "not found or not accessible", never "doesn't exist".
        raise ActionRejected(
            "GitHub refused: you may not have access there, or your admin has not "
            "accepted the App's write permissions yet."
        )
    if response.status_code == 410:
        raise ActionRejected("Issues are turned off for that repository.")
    if response.status_code >= 400:
        raise ActionError(f"GitHub answered {response.status_code}.")
    try:
        return response.json() or {}
    except ValueError as exc:
        if write:
            raise ActionOutcomeUnknown("GitHub's answer was unreadable.", cause=exc) from exc
        raise ActionError("GitHub's answer was unreadable.", cause=exc) from exc


def company_repo(org_id: str, name: str) -> str:
    """``owner/name`` if the company's GitHub installation covers it, else refuse."""
    scope = scope_from_config(get_connection_config(org_id, "github"))
    try:
        return resolve_repo(scope, name)
    except SourceError as exc:
        raise ActionRejected(
            f"{name!r} isn't a repository your company connected to Handbook."
        ) from exc


def _repo_audience(repo: dict) -> Audience:
    return Audience(public=True) if not repo.get("private", True) else Audience(members=None)


class GitHubCreateIssue(Action):
    name = "github.create_issue"
    provider = "github"
    label = "GitHub: open an issue"
    tool = {
        "type": "function",
        "function": {
            "name": "github_create_issue",
            "description": "Open a GitHub issue in a repository the user named.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_words": _REQUEST_WORDS,
                    "repo": {
                        "type": "string",
                        "description": "The repository exactly as the user wrote it: 'owner/name' or 'name'.",
                    },
                    "title": {"type": "string", "description": "A short issue title."},
                    "body": {"type": "string", "description": "The issue body, in Markdown."},
                },
                "required": ["request_words", "repo", "title", "body"],
            },
        },
    }

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        full = company_repo(ctx.org_id, require_named(args.get("repo"), ctx.question, "repository"))
        repo = _call("GET", f"/repos/{full}", ctx.token)
        if repo.get("has_issues") is False:
            raise ActionRejected("Issues are turned off for that repository.")
        return Proposal(
            action=self.name,
            target=ActionTarget("github_repo", full, full, repo.get("html_url")),
            payload={
                "title": clean_title(args.get("title")),
                "body": clean_body(args.get("body"), ctx.question, _uris(ctx)),
            },
            audience=_repo_audience(repo),
        )

    def execute(self, key, target, payload, token) -> ActionResult:
        made = _call("POST", f"/repos/{target['external_id']}/issues", token,
                     json={"title": payload["title"], "body": payload["body"]})
        if not made.get("number"):
            raise ActionError("GitHub did not open the issue.")
        return ActionResult(f"{target['external_id']}#{made['number']}", made.get("html_url"))


class GitHubComment(Action):
    name = "github.comment"
    provider = "github"
    label = "GitHub: comment on an issue or pull request"
    tool = {
        "type": "function",
        "function": {
            "name": "github_comment",
            "description": (
                "Comment on a GitHub issue or pull request the user named, as "
                "'owner/repo#123', 'repo#123' or its github.com address."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_words": _REQUEST_WORDS,
                    "issue": {
                        "type": "string",
                        "description": "The issue or pull request exactly as the user wrote it.",
                    },
                    "body": {"type": "string", "description": "The comment, in Markdown."},
                },
                "required": ["request_words", "issue", "body"],
            },
        },
    }

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        ref = require_named(args.get("issue"), ctx.question, "issue or pull request")
        match = _REF.match(ref)
        if not match:
            raise ActionRejected("Name it like owner/repo#123 or paste its GitHub address.")
        full = company_repo(ctx.org_id, match.group("repo"))
        number = int(match.group("number"))
        repo = _call("GET", f"/repos/{full}", ctx.token)
        issue = _call("GET", f"/repos/{full}/issues/{number}", ctx.token)
        kind = "pull request" if "pull_request" in issue else "issue"
        body = clean_body(args.get("body"), ctx.question, _uris(ctx))
        if not body:
            raise ActionRejected("The draft is empty.")
        return Proposal(
            action=self.name,
            target=ActionTarget(
                "github_issue", f"{full}#{number}",
                f"{full}#{number} ({kind}): {issue.get('title') or ''}".strip(),
                issue.get("html_url"),
            ),
            payload={"body": body},
            audience=_repo_audience(repo),
        )

    def execute(self, key, target, payload, token) -> ActionResult:
        full, number = target["external_id"].rsplit("#", 1)
        made = _call("POST", f"/repos/{full}/issues/{number}/comments", token,
                     json={"body": payload["body"]})
        if not made.get("id"):
            raise ActionError("GitHub did not add the comment.")
        return ActionResult(str(made["id"]), made.get("html_url"))


def _uris(ctx: ActionContext) -> list[str]:
    return [s.uri for s in ctx.sources if s.uri]
