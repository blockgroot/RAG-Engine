"""Linear implementation of the ``SourceAdapter`` interface.

Linear's API is GraphQL-only over plain HTTP, so this uses ``httpx`` directly
(already a dependency) instead of pulling in a dedicated SDK — same
dependency-light reasoning as every other adapter here. Auth is a personal
API key (``Authorization: <key>``, no "Bearer" prefix), the simplest viable
auth given there's no OAuth app to review yet — same tradeoff Notion made in
Phase 4, and the same per-org env-var discovery Notion got in Phase 9
(``LINEAR_TOKEN_<NAME>``): a key can only see the workspace it was issued in,
so the tenant boundary is enforced by Linear itself.

Each Linear *issue* (title + description + comments, flattened to text) is
one document — the natural unit, same role a Notion page plays.
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

# Same pagination cap discipline as the Notion/Drive/GitHub fetch bounds
# elsewhere in app/sources and app/githublive: bound the walk, don't trust an
# external API to paginate forever without a ceiling.
_MAX_ISSUES = 2000
_PAGE_SIZE = 100

# The listing carries each issue's TEAM and its per-issue SHARING, because
# revocation rides the listing (`ingestion.pipeline._restamp_unchanged_access`):
# a change in who may read an issue moves no `updatedAt`, so an issue whose
# audience changed is "unchanged" and is never re-fetched. Both ride the query
# already being made -- the only new calls are per TEAM (`_team_access`).
_ISSUES_QUERY = """
query Issues($after: String) {
  issues(first: %d, after: $after, orderBy: updatedAt) {
    nodes {
      id identifier title url updatedAt
      team { id }
      sharedAccess { isShared sharedWithUsers { email } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
""" % _PAGE_SIZE

# `teams` returns "all teams whose issues the user can access", so every team an
# issue in the listing belongs to is in here. `visibility` replaced the
# deprecated `private` flag: public | private | restricted.
_TEAMS_QUERY = """
query Teams($after: String) {
  teams(first: 100, after: $after) {
    nodes { id visibility }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# A team's members ARE its ACL for a non-public team. Disabled/suspended users
# are excluded by default (`includeDisabled: false`), which is what we want: a
# suspended account cannot sign in to Linear either.
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

# Linear: "Private teams are visible only to team members and workspace admins"
# (team-creation screen). Owners rank above admins. Disabled accounts are
# excluded by default, as for team members.
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

# Asks for the fields that answer the questions people actually ask about an
# issue. The description and comments alone cannot answer "what's the status of
# ENG-142?", "who's it assigned to?" or "is it done?" -- the identifier, state
# and assignee were fetched for the ACTIVITY feed and never for the indexed
# document, so those questions refused against data one field away.
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

# Issues touched since a caller-supplied instant, for activity reports (NOT
# ingestion). Two differences from _ISSUES_QUERY that both matter:
#
# 1. It filters server-side on ``updatedAt``. Linear supports this natively;
#    the listing query above simply never asked, which is why the ingestion
#    path walks every issue every time.
# 2. It asks for ``identifier``/``state``/``assignee``. A report needs to say
#    "ENG-142 moved to Done, assigned to Priya" — the issue's UUID and title
#    alone can't answer "what shipped" or "what's stuck".
#
# The whole filter is passed as one ``IssueFilter`` variable rather than
# naming the inner comparator's scalar type: Linear has renamed that scalar
# (DateTime -> DateTimeOrDuration) across API versions, and referencing the
# input object by name keeps this query working across both.
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
    }
    pageInfo { hasNextPage endCursor }
  }
}
""" % _PAGE_SIZE


def _issue_title(node: dict) -> str:
    """``ENG-142 — Fix login``, falling back to the bare title.

    The identifier is how humans refer to an issue; without it in the title,
    "what's the status of ENG-142?" has nothing to match on.
    """
    title = (node.get("title") or "Untitled issue").strip()
    identifier = (node.get("identifier") or "").strip()
    return f"{identifier} - {title}" if identifier else title


def _issue_preamble(issue: dict) -> str:
    """One prose line of issue metadata, for the top of the document.

    Only states what Linear told us -- an unset assignee is omitted rather than
    described as "unassigned", because retrieval would then happily answer
    "who is this assigned to?" with a word we invented.
    """
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
    """People, team and attached links — extra fields in the one issue query.

    `id`/`email` on assignee, creator and commenters and the `attachments`
    list cost nothing but bytes: they ride the `issue` query `fetch_document`
    already sends (Second Brain 1.1). An attachment is how Linear records a
    linked pull request, which is the Linear→GitHub edge the graph needs.
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

    Linear's per-issue sharing (Enterprise, 2026-02): a private team's issue can
    be shared with a named user who is not in the team. Empty everywhere else.
    ponytail: an issue that INHERITS sharing from its parent
    (`inheritsSharedAccess`) is trusted to report the inherited users here too;
    if it does not, those users are withheld (fail closed). Read the parent's
    `sharedAccess` in the same query if that turns out to be the case.
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
    """Fetches issues from a Linear workspace via its GraphQL API.

    Linear sends the ``Authorization`` header differently depending on the
    credential type: a personal API key is sent RAW (no scheme prefix), while
    an OAuth access token needs ``Bearer <token>``. ``oauth`` tells this
    adapter which one ``token`` is — set by ``app/sources/factory.py`` based
    on which credential path resolved it (a directly-passed ``token=`` means
    OAuth; a ``token_name``/default lookup means the legacy personal key).
    """

    def __init__(
        self,
        settings: LinearSettings | None = None,
        token: str | None = None,
        *,
        oauth: bool = False,
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
        #: team id -> visibility, filled by ONE `teams` walk per adapter (i.e.
        #: per sync). `None` until first needed, so a listing with no team ids
        #: at all (a fixture, an empty workspace) costs nothing.
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
        """Workspace admins and owners, who see every private team in Linear.

        A failure means NO admins, never a failed listing: the members are
        still a correct (narrower) audience, so an unreadable admin list locks
        admins out rather than letting anyone else in.
        """
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

        A PUBLIC team's issues are visible to every workspace member, so they are
        scope-public -- the Slack public-channel rule, and it keeps this to
        private teams only. A PRIVATE team's issues are visible to its members
        and to workspace admins (Linear: "Private teams are visible only to team
        members and workspace admins"), plus whoever the issue was individually
        shared with. RESTRICTED (a non-private team inside a
        private-team boundary) is treated like private: its parent's members can
        discover and join it, and until they join we do not grant them.

        Unreadable membership falls back to OWNER-ONLY, exactly as Drive does:
        the connected account can demonstrably read the issue, nobody else is
        granted it, and the pipeline freezes an already-indexed issue instead of
        narrowing it.
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
            data = self._query(_ISSUES_QUERY, {"after": cursor})["issues"]
            for node in data["nodes"]:
                refs.append(
                    SourceRef(
                        external_id=node["id"],
                        # "ENG-142 — Fix login" rather than "Fix login": the
                        # identifier is how people refer to an issue, and it is
                        # what makes "what's the status of ENG-142?" retrievable
                        # at all. Retrieval sees the title via the context
                        # header, so this is the cheapest place to put it.
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
        """Issues updated since ``since``, as an activity feed.

        Deliberately NOT ``list_documents``: that returns every issue as a
        ``SourceRef`` for the ingestion pipeline to chunk and embed. A
        scheduled report wants only what moved, with the state and assignee
        that make it a *report* rather than a list of titles — and stores
        nothing.

        Bounded by ``max_issues`` on top of Linear's own pagination, for the
        same reason ``_MAX_ISSUES`` bounds the listing: a busy workspace's
        month of activity must not build an unbounded prompt.
        """
        collected: list[dict] = []
        cursor: str | None = None
        # Linear's comparators take an ISO-8601 instant; normalise to UTC "Z"
        # so a naive/offset-aware datetime from the caller behaves the same.
        variables_filter = {"updatedAt": {"gt": since.isoformat()}}
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
                        # Team and the two lifecycle dates are what turn
                        # this feed into countable facts: "completed per
                        # week by team" and cycle time are impossible
                        # without them, and they ride along in a query we
                        # already make.
                        "team": (node.get("team") or {}).get("name") or "",
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

        # A metadata preamble, first, so it survives chunking: chunk 1 of a
        # long issue is the one retrieval usually returns, and status/assignee
        # are what gets asked about. Written as prose rather than a table
        # because the embedder scores prose.
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
            # The assignee, which is who the issue BELONGS to -- Linear's API
            # has no "last edited by" on an issue at all. It is the nearest
            # true statement, and provenance renders it as the editor line, so
            # "whose ticket is this?" is answerable from the context. Omitted
            # when unset, never "unassigned": an unknown editor must not reach
            # the prompt as a placeholder.
            last_editor=(issue.get("assignee") or {}).get("name") or None,
            meta=_issue_meta(issue),
        )

    def get_last_modified(self, external_id: str) -> datetime | None:
        issue = self._query(_ISSUE_QUERY, {"id": external_id})["issue"]
        if issue is None:
            raise SourceError(f"Linear issue {external_id} not found or not accessible")
        return _parse_dt(issue["updatedAt"])
