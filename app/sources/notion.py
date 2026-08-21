"""Notion implementation of the ``SourceAdapter`` interface."""

from __future__ import annotations

from datetime import datetime

from ..config.settings import IngestSanitizeSettings, NotionSettings
from ..core.exceptions import ConfigurationError, SourceError
from .base import SourceAdapter, SourceDocument, SourceRef

_INDENTING = {"bulleted_list_item", "numbered_list_item", "to_do", "toggle"}

_TRUNCATION_MARKER = "\n\n[... content truncated: page exceeds ingest size limit ...]"


def _rich_text_to_text(rich_text: list[dict]) -> str:
    """Flatten a Notion ``rich_text`` array to its plain-text content."""
    return "".join(rt.get("plain_text", "") for rt in (rich_text or []))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _page_title(page: dict) -> str:
    """Pull the human title out of a page object's properties."""
    props = page.get("properties", {}) or {}
    for prop in props.values():
        if prop.get("type") == "title":
            title = _rich_text_to_text(prop.get("title", []))
            if title:
                return title
    return "Untitled"


def _exclude_index_parents(pages: list[dict]) -> list[dict]:
    """Drop parent pages whose listed children already carry the real content."""
    ids = {page["id"] for page in pages}
    parents_of_listed: set[str] = set()
    for page in pages:
        parent = page.get("parent") or {}
        if parent.get("type") == "page_id":
            parent_id = parent.get("page_id")
            if parent_id in ids:
                parents_of_listed.add(parent_id)
    if not parents_of_listed:
        return pages
    return [page for page in pages if page["id"] not in parents_of_listed]


class NotionAdapter(SourceAdapter):
    """Fetches Notion pages the integration has been shared with."""

    def __init__(
        self, settings: NotionSettings | None = None, token: str | None = None
    ) -> None:
        """Build an adapter authenticated with a specific integration secret."""
        settings = settings or NotionSettings.from_env()
        resolved = token or settings.token
        if not resolved:
            raise ConfigurationError(
                "Missing required Notion configuration: a NOTION_TOKEN (or a per-org "
                "NOTION_TOKEN_<NAME>) Internal Integration Secret"
            )

        try:
            from notion_client import Client
        except ImportError as exc:
            raise ConfigurationError(
                "notion-client is not installed. Run: pip install -r requirements.txt",
                cause=exc,
            ) from exc

        self._settings = settings
        self._token = resolved
        self._client = Client(auth=resolved)

    def list_documents(self) -> list[SourceRef]:
        from notion_client.helpers import iterate_paginated_api

        try:
            results = list(
                iterate_paginated_api(
                    self._client.search,
                    filter={"property": "object", "value": "page"},
                )
            )
            return [
                SourceRef(
                    external_id=page["id"],
                    title=_page_title(page),
                    last_modified=_parse_dt(page.get("last_edited_time")),
                    source_uri=page.get("url"),
                )
                for page in _exclude_index_parents(results)
            ]
        except Exception as exc:  # notion APIResponseError / httpx errors
            raise SourceError(f"Notion list_documents failed: {exc}", cause=exc) from exc

    def fetch_document(self, external_id: str) -> SourceDocument:
        try:
            page = self._client.pages.retrieve(page_id=external_id)
            content = self._render_children_text(external_id)
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(
                f"Notion fetch_document({external_id}) failed: {exc}", cause=exc
            ) from exc

        return SourceDocument(
            external_id=external_id,
            title=_page_title(page),
            content=content,
            source_uri=page.get("url"),
            last_modified=_parse_dt(page.get("last_edited_time")),
        )

    def get_last_modified(self, external_id: str) -> datetime | None:
        try:
            page = self._client.pages.retrieve(page_id=external_id)
        except Exception as exc:
            raise SourceError(
                f"Notion get_last_modified({external_id}) failed: {exc}", cause=exc
            ) from exc
        return _parse_dt(page.get("last_edited_time"))

    def _render_children_text(self, block_id: str) -> str:
        """Render a page or block's children into structured plain text."""
        from notion_client.helpers import iterate_paginated_api

        budget = [IngestSanitizeSettings.from_env().max_document_chars]
        parts: list[str] = []
        for block in iterate_paginated_api(
            self._client.blocks.children.list, block_id=block_id
        ):
            if budget[0] <= 0:
                break
            lines = self._render_block(block, depth=0, budget=budget)
            if lines:
                parts.append("\n".join(lines))
        text = "\n\n".join(parts)
        if budget[0] <= 0:
            text += _TRUNCATION_MARKER
        return text

    def _render_block(self, block: dict, depth: int, budget: list[int]) -> list[str]:
        if budget[0] <= 0:
            return []
        btype = block.get("type", "")
        data = block.get(btype, {}) or {}
        indent = "  " * depth
        text = _rich_text_to_text(data.get("rich_text", []))
        lines: list[str] = []

        if btype in ("heading_1", "heading_2", "heading_3"):
            prefix = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### "}[btype]
            if text:
                lines.append(prefix + text)
        elif btype == "bulleted_list_item":
            lines.append(f"{indent}- {text}")
        elif btype == "numbered_list_item":
            lines.append(f"{indent}1. {text}")
        elif btype == "to_do":
            box = "[x]" if data.get("checked") else "[ ]"
            lines.append(f"{indent}- {box} {text}")
        elif btype == "quote":
            if text:
                lines.append(f"{indent}> {text}")
        elif btype == "code":
            lines.append(f"```{data.get('language', '')}\n{text}\n```")
        elif btype == "divider":
            lines.append("---")
        elif btype == "table_row":
            cells = data.get("cells", [])
            lines.append(f"{indent}" + " | ".join(_rich_text_to_text(c) for c in cells))
        elif btype == "child_page":
            pass
        else:
            if text:
                lines.append(indent + text)

        budget[0] -= sum(len(line) for line in lines)

        if block.get("has_children") and btype != "child_page" and budget[0] > 0:
            child_depth = depth + 1 if btype in _INDENTING else depth
            lines.extend(self._render_children_lines(block["id"], child_depth, budget))

        return lines

    def _render_children_lines(self, block_id: str, depth: int, budget: list[int]) -> list[str]:
        from notion_client.helpers import iterate_paginated_api

        lines: list[str] = []
        for block in iterate_paginated_api(
            self._client.blocks.children.list, block_id=block_id
        ):
            if budget[0] <= 0:
                break
            lines.extend(self._render_block(block, depth, budget))
        return lines
