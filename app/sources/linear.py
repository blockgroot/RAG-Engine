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

from datetime import datetime

import httpx

from ..config.settings import LinearSettings
from ..core.exceptions import ConfigurationError, SourceError
from .base import SourceAdapter, SourceDocument, SourceRef

_API_URL = "https://api.linear.app/graphql"
_TIMEOUT = 30.0

# Same pagination cap discipline as the Notion/Drive/GitHub fetch bounds
# elsewhere in app/sources and app/githublive: bound the walk, don't trust an
# external API to paginate forever without a ceiling.
_MAX_ISSUES = 2000
_PAGE_SIZE = 100

_ISSUES_QUERY = """
query Issues($after: String) {
  issues(first: %d, after: $after, orderBy: updatedAt) {
    nodes { id identifier title url updatedAt }
    pageInfo { hasNextPage endCursor }
  }
}
""" % _PAGE_SIZE

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
    assignee { name }
    team { name }
    priorityLabel
    labels { nodes { name } }
    comments {
      nodes { body user { name } createdAt }
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
            author = (comment.get("user") or {}).get("name", "someone")
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
        )

    def get_last_modified(self, external_id: str) -> datetime | None:
        issue = self._query(_ISSUE_QUERY, {"id": external_id})["issue"]
        if issue is None:
            raise SourceError(f"Linear issue {external_id} not found or not accessible")
        return _parse_dt(issue["updatedAt"])
