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
    nodes { id title url updatedAt }
    pageInfo { hasNextPage endCursor }
  }
}
""" % _PAGE_SIZE

_ISSUE_QUERY = """
query Issue($id: String!) {
  issue(id: $id) {
    id title url updatedAt description
    comments {
      nodes { body user { name } createdAt }
    }
  }
}
"""


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
                        title=node["title"],
                        last_modified=_parse_dt(node["updatedAt"]),
                        source_uri=node["url"],
                    )
                )
            page_info = data["pageInfo"]
            if not page_info["hasNextPage"]:
                break
            cursor = page_info["endCursor"]
        return refs

    def fetch_document(self, external_id: str) -> SourceDocument:
        issue = self._query(_ISSUE_QUERY, {"id": external_id})["issue"]
        if issue is None:
            raise SourceError(f"Linear issue {external_id} not found or not accessible")

        parts = [issue.get("description") or ""]
        for comment in issue["comments"]["nodes"]:
            author = (comment.get("user") or {}).get("name", "someone")
            parts.append(f"{author} commented: {comment['body']}")
        content = "\n\n".join(part for part in parts if part)

        return SourceDocument(
            external_id=issue["id"],
            title=issue["title"],
            content=content,
            source_uri=issue["url"],
            last_modified=_parse_dt(issue["updatedAt"]),
        )

    def get_last_modified(self, external_id: str) -> datetime | None:
        issue = self._query(_ISSUE_QUERY, {"id": external_id})["issue"]
        if issue is None:
            raise SourceError(f"Linear issue {external_id} not found or not accessible")
        return _parse_dt(issue["updatedAt"])
