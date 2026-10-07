"""Notion: re-read one page that retrieval already returned (plan Phase 3).

The block walk is the ingest adapter's own renderer with ONE shared char
budget (the Notion lesson: an unbounded nested walk built an unbounded string
and unbounded API calls), and it is additionally bounded in CALLS here, since
a page of empty blocks spends no characters.
"""

from __future__ import annotations

from . import base
from .base import ProviderRead

#: `blocks.children.list` calls per live read.
MAX_BLOCK_CALLS = 12

_GONE = {"object_not_found", "restricted_resource"}


def read_page(token: str, external_id: str) -> ProviderRead:
    from notion_client import APIResponseError, Client
    from notion_client.errors import HTTPResponseError, RequestTimeoutError

    from ..sources.notion import NotionAdapter, _page_title, _parse_dt

    client = Client(auth=token, timeout_ms=int(base.TIMEOUT_SECONDS * 1000))
    adapter = NotionAdapter.__new__(NotionAdapter)
    adapter._client = _CountingClient(client, MAX_BLOCK_CALLS)
    try:
        page = client.pages.retrieve(page_id=external_id)
        if page.get("archived") or page.get("in_trash"):
            return ProviderRead(base.NOT_ACCESSIBLE, reason="archived")
        budget = [base.MAX_CHARS]
        lines = adapter._render_children_lines(external_id, 0, budget, None)
    except RequestTimeoutError:
        return ProviderRead(base.TIMEOUT)
    except APIResponseError as exc:
        raw = getattr(exc, "code", "") or ""
        code = str(getattr(raw, "value", raw))  # a str Enum in notion-client
        if code in _GONE:
            return ProviderRead(base.NOT_ACCESSIBLE, reason=code)
        if code == "unauthorized":
            return ProviderRead(base.REAUTH, reason=code)
        if code == "rate_limited":
            return ProviderRead(base.RATE_LIMITED, reason=code)
        return ProviderRead(base.ERROR, reason=code or "api_error")
    except HTTPResponseError as exc:
        return ProviderRead(base.ERROR, reason=f"http_{getattr(exc, 'status', '')}")
    except Exception as exc:  # noqa: BLE001 - a live read may only ever add
        return ProviderRead(base.ERROR, reason=type(exc).__name__)

    parts = [_page_title(page)]
    edited = _parse_dt(page.get("last_edited_time"))
    if edited is not None:
        parts.append(f"Notion page, last edited {edited.strftime('%d %b %Y %H:%M UTC')}.")
    parts.append("\n".join(lines))
    if budget[0] <= 0 or adapter._client.exhausted:
        parts.append("(Live read truncated — only the first part of the page is shown.)")
    return ProviderRead(base.OK, text="\n\n".join(p for p in parts if p))


class _CountingClient:
    """The Notion client with `blocks.children.list` capped at ``limit`` calls.

    Past the cap it returns an empty page, so the adapter's renderer stops
    cleanly and ``exhausted`` tells the caller to mark the text truncated.
    """

    def __init__(self, client, limit: int) -> None:
        self._client = client
        self._left = limit
        self.exhausted = False
        self.blocks = self
        self.children = self

    def list(self, **kwargs):
        if self._left <= 0:
            self.exhausted = True
            return {"results": [], "has_more": False, "next_cursor": None}
        self._left -= 1
        return self._client.blocks.children.list(**kwargs)
