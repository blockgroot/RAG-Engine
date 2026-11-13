"""Linear ``SourceAdapter``: one issue (title, description, comments) is one document.

GraphQL over plain ``httpx``, no SDK. A token sees only its own workspace, so
Linear itself enforces the tenant boundary.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from ..config.settings import LinearSettings
from ..core.exceptions import ConfigurationError, SourceError
from .base import DocAccess, SourceAdapter, SourceDocument, SourceRef
from .meta import build_meta, container, person, resolve_link

logger = logging.getLogger(__name__)

_API_URL = "https://api.linear.app/graphql"
_TIMEOUT = 30.0

# Bound the walk; never trust an API to stop paginating.
_MAX_ISSUES = 2000
_PAGE_SIZE = 100

# The listing carries TEAM and per-issue SHARING because revocation rides the
# listing (`_restamp_unchanged_access`): an audience change moves no `updatedAt`.
_ISSUES_QUERY = """
query Issues($after: String, $filter: IssueFilter) {
  issues(first: %d, after: $after, filter: $filter, orderBy: updatedAt) {
    nodes {
      id identifier title url updatedAt
      team { id }
      sharedAccess { isShared sharedWithUsers { email } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
""" % _PAGE_SIZE

# Every team whose issues the token can read. `visibility`: public | private | restricted.
_TEAMS_QUERY = """
query Teams($after: String) {
  teams(first: 100, after: $after) {
    nodes { id key name visibility }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# A non-public team's members are its ACL. Disabled users are excluded by default.
_TEAM_MEMBERS_QUERY = """
query TeamMembers($id: String!, $after: String) {
  team(id: $id) {
    members(first: 100, after: $after) {
      nodes { email }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

# Admins and owners also see every private team (Linear's own rule).
_ADMINS_QUERY = """
query Admins($after: String) {
  users(first: 100, after: $after, filter: {or: [{admin: {eq: true}}, {owner: {eq: true}}]}) {
    nodes { email }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_VIEWER_QUERY = "query { viewer { email } }"

#: Pages of teams / of one team's members to walk. Stopping early can only
#: DROP viewers (fail closed), and it is logged so it is not silent.
_MAX_TEAM_PAGES = 5
_MAX_MEMBER_PAGES = 10

# Identifier, state and assignee are fetched so "status of ENG-142?" is answerable.
_ISSUE_QUERY = """
query Issue($id: String!) {
  issue(id: $id) {
    id identifier title url updatedAt description
    state { name type }
    assignee { id name email }
    creator { id name email }
    team { id key name }
    priorityLabel
    labels { nodes { name } }
    attachments { nodes { url } }
    comments {
      nodes { body user { id name email } createdAt }
    }
  }
}
"""

# Issues updated since an instant, for activity facts (not ingestion), filtered
# server-side. The filter is one ``IssueFilter`` variable because Linear renamed
# the inner scalar type (DateTime -> DateTimeOrDuration) across versions.
_RECENT_ISSUES_QUERY = """
query RecentIssues($after: String, $filter: IssueFilter) {
  issues(first: %d, after: $after, filter: $filter, orderBy: updatedAt) {
    nodes {
      identifier
      title
      url
      updatedAt
      createdAt
      completedAt
      state { name type }
      assignee { name }
      team { name }
      priorityLabel
      estimate
      project { name }
      labels(first: 10) { nodes { name } }
      cycle { number name }
      projectMilestone { name }
      creator { name }
      slaType
      customerTicketCount
    }
    pageInfo { hasNextPage endCursor }
  }
}
""" % _PAGE_SIZE


def _chart_fields(node: dict) -> dict:
    from ..insights.fields import simple_fields

    return simple_fields(
        node, skip=("state", "assignee", "team", "identifier", "title", "url",
                    "sharedAccess"),
    )


def _issue_title(node: dict) -> str:
    """``ENG-142 - Fix login``: people ask by identifier, so it must be in the title."""
    title = (node.get("title") or "Untitled issue").strip()
    identifier = (node.get("identifier") or "").strip()
    return f"{identifier} - {title}" if identifier else title


def _issue_preamble(issue: dict) -> str:
    """One prose line of issue metadata. An unset field is omitted, never invented."""
    state = (issue.get("state") or {}).get("name") or ""
    assignee = (issue.get("assignee") or {}).get("name") or ""
    team = (issue.get("team") or {}).get("name") or ""
    priority = issue.get("priorityLabel") or ""
    labels = [
        (node.get("name") or "").strip()
        for node in ((issue.get("labels") or {}).get("nodes") or [])
    ]

    bits = [f"Linear issue {(issue.get('identifier') or '').strip()}".strip()]
    if state:
        bits.append(f"status {state}")
    if assignee:
        bits.append(f"assigned to {assignee}")
    if team:
        bits.append(f"team {team}")
    if priority:
        bits.append(f"priority {priority}")
    if [label for label in labels if label]:
        bits.append("labels " + ", ".join(label for label in labels if label))
    return ". ".join(bits) + "."


def _issue_meta(issue: dict) -> dict | None:
    """People, team and attached links for the graph.

    An attachment is how Linear records a linked pull request (the Linear->GitHub edge).
    """
    people = []
    for field, role in (("assignee", "assignee"), ("creator", "creator")):
        user = issue.get(field) or {}
        people.append(
            person("linear", role=role, external_id=user.get("id"),
                   email=user.get("email"), name=user.get("name"))
        )
    for comment in ((issue.get("comments") or {}).get("nodes") or []):
        user = comment.get("user") or {}
        people.append(
            person("linear", role="commenter", external_id=user.get("id"),
                   email=user.get("email"), name=user.get("name"))
        )
    links = [
        resolve_link(node.get("url") or "")
        for node in ((issue.get("attachments") or {}).get("nodes") or [])
    ]
    team = issue.get("team") or {}
    return build_meta(
        people=people,
        links=links,
        containers=[container("linear", "team", team.get("id"), team.get("name"))],
    )


_UNRESOLVED = object()


def _shared_emails(node: dict) -> list[str]:
    """Who this issue was shared with individually, outside its team.

    ponytail: an issue that inherits sharing from its parent is trusted to list
    those users here; if not, they are withheld (fail closed). Read the parent's
    `sharedAccess` if that turns out to be the case.
    """
    shared = (node.get("sharedAccess") or {}).get("sharedWithUsers") or []
    return [str(u.get("email") or "").strip() for u in shared if (u.get("email") or "").strip()]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class LinearAdapter(SourceAdapter):
    """Fetches issues from a Linear workspace.

    ``oauth``: an OAuth token is sent as ``Bearer <token>``; a personal API key raw.
    """

    def __init__(
        self,
        settings: LinearSettings | None = None,
        token: str | None = None,
        *,
        oauth: bool = False,
        team_ids: list[str] | None = None,
    ) -> None:
        settings = settings or LinearSettings.from_env()
        resolved = token or settings.token
        if not resolved:
            raise ConfigurationError(
                "Missing required Linear configuration: a LINEAR_TOKEN (or a "
                "per-org LINEAR_TOKEN_<NAME>) personal API key"
            )
        self._token = resolved
        self._oauth = oauth
        #: The teams picked for this connection; ``None`` reads every team the
        #: token can see (only the legacy env-key path, never a connection).
        self._team_ids = list(team_ids) if team_ids is not None else None
        #: team id -> visibility, one `teams` walk per sync, lazily.
        self._team_visibility: dict[str, str] | None = None
        #: team id -> member emails, or None when they could not be read.
        self._team_members: dict[str, tuple[str, ...] | None] = {}
        #: Workspace admins and owners, read once and only if a non-public team
        #: shows up. ``()`` when they could not be read.
        self._workspace_admins: tuple[str, ...] | None = None
        self._account_email: object = _UNRESOLVED

    def _query(self, query: str, variables: dict | None = None) -> dict:
        auth = f"Bearer {self._token}" if self._oauth else self._token
        try:
            response = httpx.post(
                _API_URL,
                json={"query": query, "variables": variables or {}},
                headers={"Authorization": auth},
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise SourceError(f"Linear API request failed: {exc}", cause=exc) from exc
        if payload.get("errors"):
            raise SourceError(f"Linear API returned errors: {payload['errors']}")
        return payload["data"]

    def _team_filter(self) -> dict:
        return {"team": {"id": {"in": self._team_ids}}} if self._team_ids is not None else {}

    def list_teams(self) -> list[dict]:
        """Every team this token can see, for the picker:
        ``[{"id", "key", "name", "private"}]``. Raises when the walk is cut
        short, because a picker missing teams would look complete."""
        teams: list[dict] = []
        cursor: str | None = None
        for _ in range(_MAX_TEAM_PAGES):
            data = self._query(_TEAMS_QUERY, {"after": cursor})["teams"]
            teams += [
                {"id": n["id"], "key": n.get("key") or "", "name": n.get("name") or n["id"],
                 "private": (n.get("visibility") or "public") != "public"}
                for n in data["nodes"]
            ]
            if not data["pageInfo"]["hasNextPage"]:
                return teams
            cursor = data["pageInfo"]["endCursor"]
        raise SourceError(f"Linear has more than {_MAX_TEAM_PAGES * 100} teams; can't list them all")

    # -- access --------------------------------------------------------------

    def _visibility_of(self, team_id: str) -> str | None:
        """public | private | restricted, or None when it could not be read."""
        if self._team_visibility is None:
            visibility: dict[str, str] = {}
            cursor: str | None = None
            try:
                for _ in range(_MAX_TEAM_PAGES):
                    data = self._query(_TEAMS_QUERY, {"after": cursor})["teams"]
                    for node in data["nodes"]:
                        visibility[node["id"]] = str(node.get("visibility") or "")
                    if not data["pageInfo"]["hasNextPage"]:
                        break
                    cursor = data["pageInfo"]["endCursor"]
                else:
                    logger.warning("linear: team listing hit the %s-page bound", _MAX_TEAM_PAGES)
            except SourceError:
                logger.warning("linear: could not list teams; sharing unreadable", exc_info=True)
            self._team_visibility = visibility
        return self._team_visibility.get(team_id) or None

    def _members_of(self, team_id: str) -> tuple[str, ...] | None:
        if team_id in self._team_members:
            return self._team_members[team_id]
        emails: list[str] = []
        cursor: str | None = None
        members: tuple[str, ...] | None
        try:
            for _ in range(_MAX_MEMBER_PAGES):
                team = self._query(_TEAM_MEMBERS_QUERY, {"id": team_id, "after": cursor})["team"]
                if team is None:
                    raise SourceError(f"Linear team {team_id} not visible")
                data = team["members"]
                emails += [n["email"] for n in data["nodes"] if n.get("email")]
                if not data["pageInfo"]["hasNextPage"]:
                    break
                cursor = data["pageInfo"]["endCursor"]
            else:
                logger.warning(
                    "linear: team %s has more than %s pages of members; the rest "
                    "are withheld", team_id, _MAX_MEMBER_PAGES,
                )
            members = tuple(emails) or None
        except SourceError:
            logger.warning("linear: could not read members of team %s", team_id, exc_info=True)
            members = None
        self._team_members[team_id] = members
        return members

    def _admins(self) -> tuple[str, ...]:
        """Workspace admins and owners. Unreadable means none (fails closed), not a failed listing."""
        if self._workspace_admins is None:
            emails: list[str] = []
            cursor: str | None = None
            try:
                for _ in range(_MAX_MEMBER_PAGES):
                    data = self._query(_ADMINS_QUERY, {"after": cursor})["users"]
                    emails += [n["email"] for n in data["nodes"] if n.get("email")]
                    if not data["pageInfo"]["hasNextPage"]:
                        break
                    cursor = data["pageInfo"]["endCursor"]
            except SourceError:
                logger.warning("linear: could not list workspace admins", exc_info=True)
            self._workspace_admins = tuple(emails)
        return self._workspace_admins

    def _connected_account(self) -> str | None:
        """The connected account's email, for the owner-only fallback. Once, lazily."""
        if self._account_email is _UNRESOLVED:
            try:
                self._account_email = (
                    (self._query(_VIEWER_QUERY)["viewer"] or {}).get("email") or None
                )
            except SourceError:
                self._account_email = None
        return self._account_email  # type: ignore[return-value]

    def _access_for(self, node: dict) -> DocAccess | None:
        """Who may read one issue. ``None`` = could not tell AND no fallback.

        Public team: scope-public. Private or restricted team: members + admins +
        individually shared users. Unreadable membership: owner-only, as Drive.
        """
        team_id = ((node.get("team") or {}).get("id") or "").strip()
        if not team_id:
            return None
        visibility = self._visibility_of(team_id)
        if visibility == "public":
            return DocAccess.scope_public()
        members = self._members_of(team_id) if visibility else None
        if members:
            return DocAccess.restricted(list(members) + list(self._admins()) + _shared_emails(node))
        account = self._connected_account()
        return DocAccess.owner_only(account) if account else None

    # -- interface ---------------------------------------------------------

    def list_documents(self) -> list[SourceRef]:
        refs: list[SourceRef] = []
        cursor: str | None = None
        while len(refs) < _MAX_ISSUES:
            data = self._query(_ISSUES_QUERY, {"after": cursor,
                                               "filter": self._team_filter() or None})["issues"]
            for node in data["nodes"]:
                refs.append(
                    SourceRef(
                        external_id=node["id"],
                        # With the identifier, so "status of ENG-142?" retrieves it.
                        title=_issue_title(node),
                        last_modified=_parse_dt(node["updatedAt"]),
                        source_uri=node["url"],
                        access=self._access_for(node),
                    )
                )
            page_info = data["pageInfo"]
            if not page_info["hasNextPage"]:
                break
            cursor = page_info["endCursor"]
        return refs

    def fetch_recent_issues(
        self, since: datetime, *, max_issues: int = 300
    ) -> list[dict]:
        """Issues updated since ``since``, as an activity feed (not ingestion).

        Bounded by ``max_issues`` so a busy workspace cannot grow it unbounded.
        """
        collected: list[dict] = []
        cursor: str | None = None
        variables_filter = {"updatedAt": {"gt": since.isoformat()}, **self._team_filter()}
        while len(collected) < max_issues:
            data = self._query(
                _RECENT_ISSUES_QUERY,
                {"after": cursor, "filter": variables_filter},
            )["issues"]
            for node in data["nodes"]:
                state = node.get("state") or {}
                assignee = node.get("assignee") or {}
                collected.append(
                    {
                        "identifier": node.get("identifier") or "",
                        "title": node.get("title") or "",
                        "url": node.get("url") or "",
                        "state": state.get("name") or "",
                        # backlog | unstarted | started | completed | canceled
                        "state_type": state.get("type") or "",
                        "assignee": assignee.get("name") or "",
                        # Team and lifecycle dates make "completed per week by team" countable.
                        "team": (node.get("team") or {}).get("name") or "",
                        # Every simple field for charts (insights/fields.py), minus columns stored above.
                        "fields": _chart_fields(node),
                        "created_at": _parse_dt(node.get("createdAt")),
                        "completed_at": _parse_dt(node.get("completedAt")),
                        "at": _parse_dt(node.get("updatedAt")),
                    }
                )
                if len(collected) >= max_issues:
                    return collected
            page_info = data["pageInfo"]
            if not page_info["hasNextPage"]:
                break
            cursor = page_info["endCursor"]
        return collected

    def fetch_document(self, external_id: str) -> SourceDocument:
        issue = self._query(_ISSUE_QUERY, {"id": external_id})["issue"]
        if issue is None:
            raise SourceError(f"Linear issue {external_id} not found or not accessible")

        # Metadata first, as prose, so it lands in chunk 1 and embeds well.
        parts = [_issue_preamble(issue), issue.get("description") or ""]
        for comment in issue["comments"]["nodes"]:
            author = (comment.get("user") or {}).get("name") or "someone"
            parts.append(f"{author} commented: {comment['body']}")
        content = "\n\n".join(part for part in parts if part)

        return SourceDocument(
            external_id=issue["id"],
            title=_issue_title(issue),
            content=content,
            source_uri=issue["url"],
            last_modified=_parse_dt(issue["updatedAt"]),
            # Linear has no "last edited by"; the assignee is the nearest truth. Omitted when unset.
            last_editor=(issue.get("assignee") or {}).get("name") or None,
            meta=_issue_meta(issue),
        )

    def get_last_modified(self, external_id: str) -> datetime | None:
        issue = self._query(_ISSUE_QUERY, {"id": external_id})["issue"]
        if issue is None:
            raise SourceError(f"Linear issue {external_id} not found or not accessible")
        return _parse_dt(issue["updatedAt"])


def validate_linear_teams(token: str, team_ids: list[str]) -> dict:
    """The picked teams as a connection's config: ``{team_ids, team_names}``.
    Only teams this token can see; at least one."""
    if not team_ids:
        raise ConfigurationError("Pick at least one Linear team.")
    known = {t["id"]: t for t in LinearAdapter(token=token, oauth=True).list_teams()}
    unknown = [t for t in team_ids if t not in known]
    if unknown:
        raise ConfigurationError("One or more of these Linear teams isn't visible to this connection.")
    ids = list(dict.fromkeys(team_ids))
    return {"team_ids": ids, "team_names": {t: known[t]["name"] for t in ids}}
