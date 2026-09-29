"""Linear: re-read one issue that retrieval already returned (plan Phase 1).

Read-only, one fixed query, by the issue's own id from our ``documents`` row --
never a search. Decides failures by LINEAR'S reason, never the status code
alone: Linear answers a deleted or unreadable issue with HTTP 200 and a GraphQL
``Entity not found`` error, and a rate limit with HTTP 400 + ``RATELIMITED``.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from ..sources.linear import _API_URL, _issue_preamble, _issue_title, _parse_dt
from . import base
from .base import ProviderRead

_LIVE_ISSUE_QUERY = """
query LiveIssue($id: String!) {
  issue(id: $id) {
    identifier title updatedAt description
    state { name type }
    assignee { name }
    team { name }
    priorityLabel
    labels { nodes { name } }
    comments(first: 50) { nodes { body createdAt user { name } } }
  }
}
"""

#: Linear error codes (``extensions.code``) and what each means for the stale copy.
_PERMISSION_CODES = {"FORBIDDEN"}
_AUTH_CODES = {"AUTHENTICATION_ERROR"}
_RATE_CODES = {"RATELIMITED"}


def read_issue(token: str, external_id: str) -> ProviderRead:
    """One issue, current. ``token`` lives only inside this call's header."""
    try:
        response = httpx.post(
            _API_URL,
            json={"query": _LIVE_ISSUE_QUERY, "variables": {"id": external_id}},
            headers={"Authorization": f"Bearer {token}"},
            timeout=base.TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        return ProviderRead(base.TIMEOUT)
    except httpx.HTTPError as exc:
        return ProviderRead(base.ERROR, reason=type(exc).__name__)

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        return _classify_errors(errors)
    if response.status_code == 401:
        return ProviderRead(base.REAUTH, reason="http_401")
    if response.status_code == 429:
        return ProviderRead(base.RATE_LIMITED, reason="http_429")
    if response.status_code == 404:
        return ProviderRead(base.NOT_ACCESSIBLE, reason="http_404")
    if response.status_code >= 400:
        # 403 with no reason we recognise, or a 5xx: fall back and log it,
        # never withhold on a guess (plan §5).
        return ProviderRead(base.ERROR, reason=f"http_{response.status_code}")

    issue = ((payload or {}).get("data") or {}).get("issue")
    if issue is None:
        return ProviderRead(base.NOT_ACCESSIBLE, reason="issue_null")
    return ProviderRead(base.OK, text=_render(issue))


def _classify_errors(errors: list) -> ProviderRead:
    codes, messages = set(), []
    for err in errors:
        if not isinstance(err, dict):
            continue
        ext = err.get("extensions") or {}
        if ext.get("code"):
            codes.add(str(ext["code"]).upper())
        messages.append(str(err.get("message") or ""))
    joined = " ".join(messages).lower()
    reason = ",".join(sorted(codes)) or "graphql_error"
    if codes & _RATE_CODES:
        return ProviderRead(base.RATE_LIMITED, reason=reason)
    if codes & _AUTH_CODES:
        return ProviderRead(base.REAUTH, reason=reason)
    if "entity not found" in joined or codes & _PERMISSION_CODES:
        return ProviderRead(base.NOT_ACCESSIBLE, reason=reason or "entity_not_found")
    return ProviderRead(base.ERROR, reason=reason)


def _render(issue: dict) -> str:
    """The issue as prose, newest comments last, bounded by the caller."""
    parts = [_issue_title(issue), _issue_preamble(issue)]
    updated = _parse_dt(issue.get("updatedAt"))
    if updated is not None:
        parts.append(f"Last updated {_when(updated)}.")
    description = (issue.get("description") or "").strip()
    if description:
        parts.append(description)
    comments = [
        c for c in ((issue.get("comments") or {}).get("nodes") or []) if isinstance(c, dict)
    ]
    comments.sort(key=lambda c: c.get("createdAt") or "")
    newest = comments[-base.LINEAR_COMMENTS:]
    for comment in newest:
        author = (comment.get("user") or {}).get("name") or "someone"
        created = _parse_dt(comment.get("createdAt"))
        stamp = f" ({_when(created)})" if created else ""
        parts.append(f"{author} commented{stamp}: {(comment.get('body') or '').strip()}")
    if len(comments) > len(newest):
        parts.append(f"({len(comments) - len(newest)} older comments not shown.)")
    return "\n\n".join(p for p in parts if p)


def _when(moment: datetime) -> str:
    return moment.strftime("%d %b %Y %H:%M UTC")
