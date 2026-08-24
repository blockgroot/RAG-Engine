"""Confluence Cloud implementation of the ``SourceAdapter`` interface.

Confluence Cloud's REST API is plain HTTPS + HTTP Basic (email + API token),
so this uses ``httpx`` directly — same dependency-light reasoning as every
other adapter here. There is no official/lightweight Python SDK worth adding
(the same call ``notion-client`` was skipped for a heavier LlamaIndex reader
in Phase 4), and Confluence pages come back as HTML, so this converts them to
plain text with the stdlib ``html.parser`` rather than adding a parsing
dependency (no bs4/lxml in requirements — see the ladder: stdlib does it).

Each Confluence *page* is one document — the same role a Notion page plays.
"""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser

import httpx

from ..config.settings import ConfluenceCredential, ConfluenceSettings
from ..core.exceptions import ConfigurationError, SourceError
from .base import SourceAdapter, SourceDocument, SourceRef

_TIMEOUT = 30.0
# Same pagination-ceiling discipline as Notion/Drive/Linear: bound the walk,
# don't trust the API to paginate forever with no cap.
_MAX_PAGES = 2000
_PAGE_SIZE = 100

_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "ul", "ol",
}


class _HtmlToText(HTMLParser):
    """Minimal HTML→text: strips tags, keeps block-level line breaks."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [line.strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def _html_to_text(html: str) -> str:
    parser = _HtmlToText()
    parser.feed(html or "")
    return parser.text()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class ConfluenceAdapter(SourceAdapter):
    """Fetches pages from a Confluence Cloud site via its REST API."""

    def __init__(
        self,
        settings: ConfluenceSettings | None = None,
        credential: ConfluenceCredential | None = None,
    ) -> None:
        settings = settings or ConfluenceSettings.from_env()
        resolved = credential or (
            ConfluenceCredential(settings.base_url, settings.email, settings.token)
            if settings.base_url and settings.email and settings.token
            else None
        )
        if not resolved:
            raise ConfigurationError(
                "Missing required Confluence configuration: CONFLUENCE_BASE_URL / "
                "_EMAIL / _TOKEN (or a per-org CONFLUENCE_BASE_URL_<NAME> / "
                "_EMAIL_<NAME> / _TOKEN_<NAME>)"
            )
        self._credential = resolved

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._credential.base_url.rstrip('/')}{path}"
        try:
            response = httpx.get(
                url,
                params=params,
                auth=(self._credential.email, self._credential.token),
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise SourceError(f"Confluence API request to {path} failed: {exc}", cause=exc) from exc

    def _page_url(self, page: dict) -> str:
        webui = ((page.get("_links") or {}).get("webui")) or ""
        return f"{self._credential.base_url.rstrip('/')}{webui}"

    # -- interface ---------------------------------------------------------

    def list_documents(self) -> list[SourceRef]:
        refs: list[SourceRef] = []
        cursor: str | None = None
        while len(refs) < _MAX_PAGES:
            params = {"limit": _PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/api/v2/pages", params=params)
            for page in data.get("results", []):
                refs.append(
                    SourceRef(
                        external_id=page["id"],
                        title=page.get("title", "Untitled"),
                        last_modified=_parse_dt(page.get("version", {}).get("createdAt")),
                        source_uri=self._page_url(page),
                    )
                )
            next_link = (data.get("_links") or {}).get("next")
            if not next_link:
                break
            cursor = httpx.URL(next_link).params.get("cursor")
            if not cursor:
                break
        return refs

    def fetch_document(self, external_id: str) -> SourceDocument:
        page = self._get(
            f"/api/v2/pages/{external_id}",
            params={"body-format": "export_view"},
        )
        html = ((page.get("body") or {}).get("export_view") or {}).get("value", "")
        return SourceDocument(
            external_id=page["id"],
            title=page.get("title", "Untitled"),
            content=_html_to_text(html),
            source_uri=self._page_url(page),
            last_modified=_parse_dt(page.get("version", {}).get("createdAt")),
        )

    def get_last_modified(self, external_id: str) -> datetime | None:
        page = self._get(f"/api/v2/pages/{external_id}")
        return _parse_dt(page.get("version", {}).get("createdAt"))
