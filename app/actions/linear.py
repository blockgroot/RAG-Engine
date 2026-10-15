"""Linear actions: create an issue, comment on one. Both run as the asker.

The asker's token comes from their own Linear consent (``actor=user``, scopes
``read,issues:create,comments:create``), so Linear itself decides whether they
may write to a team. We still resolve the team ourselves first: Linear
documents no specific error for a team the person cannot reach, and a clear
"you can't file in that team" beats parsing one.

Each create sends the ``actions`` row id as Linear's client ``id`` ("If none
is provided, the backend will generate one"), so a repeated send cannot make
a second issue or comment.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..core.exceptions import (
    ActionError,
    ActionOutcomeUnknown,
    ActionRejected,
    OAuthReauthRequiredError,
)
from .base import Action, ActionContext, ActionResult, ActionTarget, Audience, Proposal
from .draft import clean_body, clean_title, require_named

GRAPHQL_URL = "https://api.linear.app/graphql"
#: What a person grants when they connect Linear for actions. Never ``write``
#: or ``admin``: create-only scopes cannot edit, close or delete anything.
WRITE_SCOPES = "read,issues:create,comments:create"
_TIMEOUT = 15.0
#: Members read for an audience. More than this and the audience is unknown.
MEMBER_LIMIT = 250
_IDENTIFIER = re.compile(r"\b[A-Za-z][A-Za-z0-9]{0,9}-\d{1,7}\b")

_REQUEST_WORDS = {
    "type": "string",
    "description": (
        "The exact words from the user's message that ask for this action, "
        "copied verbatim (for example 'file an issue')."
    ),
}


def gql(token: str, query: str, variables: dict[str, Any] | None = None, *, write: bool = False) -> dict:
    """One GraphQL call with the asker's token.

    A read that fails is a refusal; a WRITE that fails without an answer is
    ``ActionOutcomeUnknown``, because the issue may exist.
    """
    try:
        response = httpx.post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        if write:
            raise ActionOutcomeUnknown("Linear did not answer.", cause=exc) from exc
        raise ActionError("Could not reach Linear.", cause=exc) from exc
    if response.status_code == 401:
        raise OAuthReauthRequiredError("Reconnect your Linear account.")
    if write and response.status_code >= 500:
        raise ActionOutcomeUnknown(f"Linear answered {response.status_code}.")
    try:
        payload = response.json()
    except ValueError as exc:
        if write:
            raise ActionOutcomeUnknown("Linear's answer was unreadable.", cause=exc) from exc
        raise ActionError("Linear's answer was unreadable.", cause=exc) from exc
    if payload.get("errors"):
        message = (payload["errors"][0] or {}).get("message") or "Linear refused it."
        raise ActionError(f"Linear: {message}")
    return payload.get("data") or {}


def viewer(token: str) -> dict:
    """Who this token is and which Linear workspace it belongs to."""
    data = gql(token, "query { viewer { id name email } organization { id name } }")
    return {"viewer": data.get("viewer") or {}, "organization": data.get("organization") or {}}


def _active_emails(nodes: list[dict]) -> tuple[str, ...]:
    return tuple((n.get("email") or "") for n in nodes if n.get("active", True))


def team_audience(token: str, team: dict) -> Audience:
    """Who can read a team's issues.

    A private team: its members. A public team: every active person in the
    Linear workspace ("Anyone in the workspace can create issues for other
    teams"). A ``restricted`` team sits inside a private boundary we cannot
    list, so it is unknown. A list cut at ``MEMBER_LIMIT`` is unknown too.
    """
    visibility = (team.get("visibility") or "").lower()
    if visibility == "private":
        data = gql(
            token,
            "query($id: String!, $n: Int!) { team(id: $id) { members(first: $n) "
            "{ nodes { email active } pageInfo { hasNextPage } } } }",
            {"id": team["id"], "n": MEMBER_LIMIT},
        )
        members = ((data.get("team") or {}).get("members")) or {}
    elif visibility == "public":
        data = gql(
            token,
            "query($n: Int!) { users(first: $n) { nodes { email active } "
            "pageInfo { hasNextPage } } }",
            {"n": MEMBER_LIMIT},
        )
        members = data.get("users") or {}
    else:
        return Audience(members=None)
    if (members.get("pageInfo") or {}).get("hasNextPage"):
        return Audience(members=None)
    return Audience(members=_active_emails(members.get("nodes") or []))


def _team_for(token: str, name: str) -> dict:
    """The team the asker named, among those their token can see."""
    data = gql(
        token,
        "query { teams(first: 100) { nodes { id key name visibility } } }",
    )
    wanted = name.casefold()
    for team in (data.get("teams") or {}).get("nodes") or []:
        if wanted in ((team.get("key") or "").casefold(), (team.get("name") or "").casefold()):
            return team
    raise ActionRejected(
        f"You don't have a Linear team called {name!r} that you can file in."
    )


class LinearCreateIssue(Action):
    name = "linear.create_issue"
    provider = "linear"
    label = "Linear: create an issue"
    tool = {
        "type": "function",
        "function": {
            "name": "linear_create_issue",
            "description": "Create a Linear issue in a team the user named.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_words": _REQUEST_WORDS,
                    "team": {
                        "type": "string",
                        "description": "The team's name or key, exactly as the user wrote it.",
                    },
                    "title": {"type": "string", "description": "A short issue title."},
                    "body": {
                        "type": "string",
                        "description": "The issue description, in Markdown.",
                    },
                },
                "required": ["request_words", "team", "title", "body"],
            },
        },
    }

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        name = require_named(args.get("team"), ctx.question, "Linear team")
        team = _team_for(ctx.token, name)
        return Proposal(
            action=self.name,
            target=ActionTarget("linear_team", team["id"], f"{team.get('name')} ({team.get('key')})"),
            payload={
                "title": clean_title(args.get("title")),
                "body": clean_body(args.get("body"), ctx.question, _uris(ctx)),
            },
            audience=team_audience(ctx.token, team),
        )

    def execute(self, key, target, payload, token) -> ActionResult:
        data = gql(
            token,
            "mutation($input: IssueCreateInput!) { issueCreate(input: $input) "
            "{ success issue { id identifier url } } }",
            {"input": {"id": key, "teamId": target["external_id"],
                       "title": payload["title"], "description": payload["body"]}},
            write=True,
        )
        issue = (data.get("issueCreate") or {}).get("issue") or {}
        if not issue.get("id"):
            raise ActionError("Linear did not create the issue.")
        return ActionResult(issue.get("identifier") or issue["id"], issue.get("url"))


class LinearComment(Action):
    name = "linear.comment"
    provider = "linear"
    label = "Linear: comment on an issue"
    tool = {
        "type": "function",
        "function": {
            "name": "linear_comment",
            "description": "Add a comment to a Linear issue the user named by its identifier (e.g. ENG-42).",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_words": _REQUEST_WORDS,
                    "issue": {
                        "type": "string",
                        "description": "The issue identifier exactly as the user wrote it, e.g. ENG-42.",
                    },
                    "body": {"type": "string", "description": "The comment, in Markdown."},
                },
                "required": ["request_words", "issue", "body"],
            },
        },
    }

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        ident = require_named(args.get("issue"), ctx.question, "Linear issue")
        if not _IDENTIFIER.fullmatch(ident):
            raise ActionRejected("Name the Linear issue by its identifier, like ENG-42.")
        data = gql(
            ctx.token,
            "query($id: String!) { issue(id: $id) { id identifier title url "
            "team { id name key visibility } } }",
            {"id": ident.upper()},
        )
        issue = data.get("issue")
        if not issue:
            raise ActionRejected(f"You can't see a Linear issue {ident.upper()}.")
        return Proposal(
            action=self.name,
            target=ActionTarget("linear_issue", issue["id"],
                                f"{issue['identifier']}: {issue.get('title') or ''}".strip(),
                                issue.get("url")),
            payload={"body": _required_body(args, ctx)},
            audience=team_audience(ctx.token, issue.get("team") or {}),
        )

    def execute(self, key, target, payload, token) -> ActionResult:
        data = gql(
            token,
            "mutation($input: CommentCreateInput!) { commentCreate(input: $input) "
            "{ success comment { id url } } }",
            {"input": {"id": key, "issueId": target["external_id"], "body": payload["body"]}},
            write=True,
        )
        comment = (data.get("commentCreate") or {}).get("comment") or {}
        if not comment.get("id"):
            raise ActionError("Linear did not add the comment.")
        return ActionResult(comment["id"], comment.get("url"))


def _uris(ctx: ActionContext) -> list[str]:
    return [s.uri for s in ctx.sources if s.uri]


def _required_body(args: dict[str, Any], ctx: ActionContext) -> str:
    body = clean_body(args.get("body"), ctx.question, _uris(ctx))
    if not body:
        raise ActionRejected("The draft is empty.")
    return body
