"""Linear actions: create an issue, comment on one, update one. All run as the asker.

The asker's token comes from their own Linear consent (``actor=user``, scopes
``read,issues:create,comments:create``, plus ``write`` only when an admin
switched "update an issue" on: Linear has no narrower scope for changing an
issue), so Linear itself decides whether they may write to a team. We still resolve the team ourselves first: Linear
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
from .base import Action, ActionContext, ActionResult, ActionTarget, Audience, Proposal, details
from .draft import asked_in, clean_body, clean_title, require_named

GRAPHQL_URL = "https://api.linear.app/graphql"
#: What a person grants when they connect Linear for actions. Never ``admin``.
#: ``write`` (which could also archive) is asked only when updating is on
#: (``scopes_for``); our code still sends only ``issueUpdate``.
WRITE_SCOPES = "read,issues:create,comments:create"
UPDATE_ACTION = "linear.update_issue"


def scopes_for(org_id: str) -> str:
    """The scopes a sign-in asks for in this company right now."""
    from . import policy

    on = UPDATE_ACTION in policy.enabled_actions(org_id, None)
    return f"{WRITE_SCOPES},write" if on else WRITE_SCOPES
_TIMEOUT = 15.0
#: Members read for an audience. More than this and the audience is unknown.
MEMBER_LIMIT = 250
_IDENTIFIER = re.compile(r"\b[A-Za-z][A-Za-z0-9]{0,9}-\d{1,7}\b")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Linear's priority numbers. 0 ("no priority") is the default, never asked for.
PRIORITIES = {"urgent": 1, "high": 2, "medium": 3, "low": 4}
#: Labels, members or projects listed to match a name. Past this, refuse.
LIST_LIMIT = 250

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


def _optional(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    return value.strip() if isinstance(value, str) else ""


def _one(nodes: list[dict], wanted: str, what: str, *keys: str) -> dict:
    """The single node whose name (or other key) is what the asker wrote."""
    folded = wanted.casefold().lstrip("@")
    found = [n for n in nodes if any((n.get(k) or "").casefold() == folded for k in keys)]
    if not found:
        raise ActionRejected(f"I can't find a {what} called {wanted!r} that you can use here.")
    if len(found) > 1:
        raise ActionRejected(f"More than one {what} is called {wanted!r}. Be more specific.")
    return found[0]


def _parent_issue(token: str, ident: str) -> dict:
    if not _IDENTIFIER.fullmatch(ident):
        raise ActionRejected("Name the parent issue by its identifier, like ENG-42.")
    issue = gql(token, "query($id: String!) { issue(id: $id) { id identifier title "
                       "team { id key name visibility } } }", {"id": ident.upper()}).get("issue")
    if not issue:
        raise ActionRejected(f"You can't see a Linear issue {ident.upper()}.")
    return issue


def _settings(args: dict[str, Any], ctx: ActionContext, team: dict, parent: dict | None,
              *, label_key: str = "labelIds"):
    """The optional settings, each only when the asker's own words chose it,
    each resolved among what their token can see in this team. Nothing is
    created: an unknown label, person or project is refused, not made up."""
    q, chosen, shown = ctx.question, {}, []
    if parent:
        chosen["parentId"] = parent["id"]
        shown.append(("Sub-issue of", f"{parent['identifier']}: {parent.get('title') or ''}".strip()))

    assignee = _optional(args, "assignee")
    if assignee:
        require_named(assignee, q, "assignee")
        if assignee.casefold() in ("me", "myself"):
            # "me" is inside "meeting": the word itself must be in the request.
            if not re.search(r"\b(me|myself)\b", q, re.IGNORECASE):
                raise ActionRejected("Name the assignee in your request, so I know you chose it.")
            me = viewer(ctx.token)["viewer"]
            person = {"id": me.get("id"), "name": me.get("name")}
        else:
            data = gql(ctx.token, "query($id: String!, $n: Int!) { team(id: $id) { members(first: $n) "
                                  "{ nodes { id name displayName email active } } } }",
                       {"id": team["id"], "n": LIST_LIMIT})
            members = [m for m in (((data.get("team") or {}).get("members") or {}).get("nodes") or [])
                       if m.get("active", True)]
            person = _one(members, assignee, "member of this team", "name", "displayName", "email")
        chosen["assigneeId"] = person["id"]
        shown.append(("Assignee", person.get("name") or assignee))

    priority = _optional(args, "priority").casefold()
    if priority:
        if priority not in PRIORITIES or not asked_in(priority, q):
            raise ActionRejected("Say the priority in your request: urgent, high, medium or low.")
        chosen["priority"] = PRIORITIES[priority]
        shown.append(("Priority", priority.title()))

    labels = [x.strip() for x in (args.get("labels") or []) if isinstance(x, str) and x.strip()]
    if labels:
        for label in labels:
            require_named(label, q, "label")
        data = gql(ctx.token, "query($n: Int!) { issueLabels(first: $n) { nodes { id name team { id } } } }",
                   {"n": LIST_LIMIT})
        usable = [n for n in ((data.get("issueLabels") or {}).get("nodes") or [])
                  if not n.get("team") or n["team"].get("id") == team["id"]]
        picked = [_one(usable, label, "label", "name") for label in labels]
        chosen[label_key] = [n["id"] for n in picked]
        shown.append(("Labels", ", ".join(n["name"] for n in picked)))

    project = _optional(args, "project")
    if project:
        require_named(project, q, "project")
        data = gql(ctx.token, "query($id: String!, $n: Int!) { team(id: $id) { projects(first: $n) "
                              "{ nodes { id name } } } }", {"id": team["id"], "n": LIST_LIMIT})
        found = _one(((data.get("team") or {}).get("projects") or {}).get("nodes") or [],
                     project, "project in this team", "name")
        chosen["projectId"] = found["id"]
        shown.append(("Project", found["name"]))

    due = _optional(args, "due_date")
    if due:
        # The model turns "due Friday" into a date; the words must be the asker's.
        if not _DATE.match(due) or not asked_in(_optional(args, "due_words"), q):
            raise ActionRejected("Say the due date in your request, like 'due 2026-10-30'.")
        chosen["dueDate"] = due
        shown.append(("Due", due))
    return chosen, details(*shown)


class LinearCreateIssue(Action):
    name = "linear.create_issue"
    provider = "linear"
    label = "Linear: create an issue"
    tool = {
        "type": "function",
        "function": {
            "name": "linear_create_issue",
            "description": (
                "Create a Linear issue in a team the user named, or a sub-issue under an "
                "issue they named. Fill an optional setting ONLY when the user asked for it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_words": _REQUEST_WORDS,
                    "team": {
                        "type": "string",
                        "description": "The team's name or key, exactly as the user wrote it. "
                                       "May be left empty for a sub-issue (it joins the parent's team).",
                    },
                    "parent": {"type": "string", "description":
                               "For a sub-issue: the parent's identifier exactly as written, e.g. ENG-42."},
                    "title": {"type": "string", "description": "A short issue title."},
                    "body": {
                        "type": "string",
                        "description": "The issue description, in Markdown.",
                    },
                    "assignee": {"type": "string", "description":
                                 "The person exactly as the user wrote them, or 'me'."},
                    "priority": {"type": "string", "enum": list(PRIORITIES),
                                 "description": "Only the word the user used."},
                    "labels": {"type": "array", "items": {"type": "string"},
                               "description": "Label names exactly as the user wrote them."},
                    "project": {"type": "string", "description": "The project exactly as the user wrote it."},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD."},
                    "due_words": {"type": "string", "description":
                                  "The user's words that gave the due date, copied verbatim."},
                },
                "required": ["request_words", "title", "body"],
            },
        },
    }

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        parent_ident = _optional(args, "parent")
        parent = _parent_issue(ctx.token, require_named(parent_ident, ctx.question, "parent issue")) \
            if parent_ident else None
        if _optional(args, "team") or parent is None:
            team = _team_for(ctx.token, require_named(args.get("team"), ctx.question, "Linear team"))
        else:
            team = parent.get("team") or {}
        chosen, shown = _settings(args, ctx, team, parent)
        return Proposal(
            action=self.name,
            target=ActionTarget("linear_team", team["id"], f"{team.get('name')} ({team.get('key')})"),
            payload={
                "title": clean_title(args.get("title")),
                "body": clean_body(args.get("body"), ctx.question, _uris(ctx)),
                **({"set": chosen, "details": shown} if chosen else {}),
            },
            audience=team_audience(ctx.token, team),
        )

    def execute(self, key, target, payload, token) -> ActionResult:
        data = gql(
            token,
            "mutation($input: IssueCreateInput!) { issueCreate(input: $input) "
            "{ success issue { id identifier url } } }",
            {"input": {"id": key, "teamId": target["external_id"],
                       "title": payload["title"], "description": payload["body"],
                       **(payload.get("set") or {})}},
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


class LinearUpdateIssue(Action):
    """Change an existing issue's status, assignee, priority, labels, project or
    due date. Every value is one the asker wrote, resolved among what exists in
    the issue's team; labels are ADDED, never removed. No text is drafted."""

    name = UPDATE_ACTION
    provider = "linear"
    label = "Linear: update an issue"
    editable_fields = ()
    tool = {
        "type": "function",
        "function": {
            "name": "linear_update_issue",
            "description": (
                "Change a Linear issue the user named by its identifier (e.g. ENG-42): its "
                "status, assignee, priority, labels to add, project or due date. Fill ONLY "
                "what the user asked to change."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_words": _REQUEST_WORDS,
                    "issue": {"type": "string", "description":
                              "The issue identifier exactly as the user wrote it, e.g. ENG-42."},
                    "status": {"type": "string", "description": "The status exactly as the user wrote it."},
                    "assignee": {"type": "string", "description":
                                 "The person exactly as the user wrote them, or 'me'."},
                    "priority": {"type": "string", "enum": list(PRIORITIES),
                                 "description": "Only the word the user used."},
                    "labels": {"type": "array", "items": {"type": "string"},
                               "description": "Labels to add, exactly as the user wrote them."},
                    "project": {"type": "string", "description": "The project exactly as the user wrote it."},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD."},
                    "due_words": {"type": "string", "description":
                                  "The user's words that gave the due date, copied verbatim."},
                },
                "required": ["request_words", "issue"],
            },
        },
    }

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        if not _granted_write(ctx):
            raise ActionRejected(
                "Your Linear sign-in can't update issues yet. Reconnect Linear under "
                "Account → Agent to allow it.")
        ident = require_named(args.get("issue"), ctx.question, "Linear issue")
        if not _IDENTIFIER.fullmatch(ident):
            raise ActionRejected("Name the Linear issue by its identifier, like ENG-42.")
        issue = gql(ctx.token, "query($id: String!) { issue(id: $id) { id identifier title url "
                               "state { name } team { id key name visibility } } }",
                    {"id": ident.upper()}).get("issue")
        if not issue:
            raise ActionRejected(f"You can't see a Linear issue {ident.upper()}.")
        team = issue.get("team") or {}
        chosen, shown = _settings(args, ctx, team, None, label_key="addedLabelIds")
        status = _optional(args, "status")
        if status:
            require_named(status, ctx.question, "status")
            data = gql(ctx.token, "query($id: String!) { team(id: $id) { states { nodes { id name } } } }",
                       {"id": team["id"]})
            state = _one(((data.get("team") or {}).get("states") or {}).get("nodes") or [],
                         status, "status in this team", "name")
            chosen["stateId"] = state["id"]
            was = (issue.get("state") or {}).get("name")
            shown = details(("Status", f"{state['name']}" + (f" (was {was})" if was else ""))) + shown
        if not chosen:
            raise ActionRejected("Say what to change on the issue: status, assignee, priority, "
                                 "labels, project or due date.")
        shown = [["Add labels" if k == "Labels" else k, v] for k, v in shown]
        return Proposal(
            action=self.name,
            target=ActionTarget("linear_issue", issue["id"],
                                f"{issue['identifier']}: {issue.get('title') or ''}".strip(),
                                issue.get("url")),
            payload={"set": chosen, "details": shown},
            audience=team_audience(ctx.token, team),
        )

    def execute(self, key, target, payload, token) -> ActionResult:
        data = gql(
            token,
            "mutation($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) "
            "{ success issue { id identifier url } } }",
            {"id": target["external_id"], "input": payload.get("set") or {}},
            write=True,
        )
        issue = (data.get("issueUpdate") or {}).get("issue") or {}
        if not (data.get("issueUpdate") or {}).get("success") or not issue.get("id"):
            raise ActionError("Linear did not update the issue.")
        return ActionResult(issue.get("identifier") or issue["id"], issue.get("url"))


def _granted_write(ctx: ActionContext) -> bool:
    """The person's Linear sign-in includes ``write`` (asked only once updating was on)."""
    from ..auth.user_connections import list_user_connections

    for conn in list_user_connections(ctx.org_id, ctx.user_id):
        if conn.provider == "linear":
            return "write" in (conn.scopes or ())
    return False


def _uris(ctx: ActionContext) -> list[str]:
    return [s.uri for s in ctx.sources if s.uri]


def _required_body(args: dict[str, Any], ctx: ActionContext) -> str:
    body = clean_body(args.get("body"), ctx.question, _uris(ctx))
    if not body:
        raise ActionRejected("The draft is empty.")
    return body
