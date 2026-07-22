"""Notion implementation of the ``SourceAdapter`` interface.

Uses the official-community ``notion-client`` SDK (a thin wrapper over Notion's
HTTP API, whose only dependency is ``httpx``). We chose this over
``llama-index-readers-notion`` deliberately: that reader pulls in the whole
``llama-index-core`` (~29 transitive deps) and returns LlamaIndex ``Document``
objects we would immediately unwrap, whereas we already own preprocessing,
chunking, embedding, and storage — we only need the raw-content-fetching piece.
The SDK hands us plain API dicts, so we keep full control of block→text
conversion (see ``_render_block``) and stay dependency-light, exactly the same
reasoning that put a plain OpenAI client (not a framework) behind ``LLMProvider``.

Auth: a Notion *internal integration secret* (static token). The same SDK
transparently accepts an OAuth access token later, so the multi-tenant OAuth
phase swaps only how the token is obtained, not this adapter.

Notion returns content as a tree of typed *blocks*. ``_render_block`` converts
the common block types into Markdown-ish plain text (headings as ``#``, bullets
as ``-``, tables as ``|``-separated rows), recursing into nested children — which
keeps structure the chunker already understands.
"""

from __future__ import annotations

from datetime import datetime

from ..config.settings import NotionSettings
from ..core.exceptions import ConfigurationError, SourceError
from .base import SourceAdapter, SourceDocument, SourceRef

# Block types whose children should be indented one level (nested lists/toggles).
_INDENTING = {"bulleted_list_item", "numbered_list_item", "to_do", "toggle"}


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


class NotionAdapter(SourceAdapter):
    """Fetches Notion pages the integration has been shared with."""

    def __init__(
        self, settings: NotionSettings | None = None, token: str | None = None
    ) -> None:
        """Build an adapter authenticated with a specific integration secret.

        ``token`` is the exact secret to use for this run (one organization's own
        integration). When omitted, the default ``NOTION_TOKEN`` from ``settings``
        is used — preserving the single-token Phase 4 behaviour. Passing ``token``
        is how Phase 9 points ingestion at a specific org's credential without any
        global/shared fallback (see ``NotionSettings.resolve_token``).
        """
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
        self._token = resolved  # the exact secret this adapter authenticates with
        self._client = Client(auth=resolved)

    # -- interface ---------------------------------------------------------

    def list_documents(self) -> list[SourceRef]:
        from notion_client.helpers import iterate_paginated_api

        try:
            results = iterate_paginated_api(
                self._client.search,
                filter={"property": "object", "value": "page"},
            )
            return [
                SourceRef(
                    external_id=page["id"],
                    title=_page_title(page),
                    last_modified=_parse_dt(page.get("last_edited_time")),
                    source_uri=page.get("url"),
                )
                for page in results
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

    # -- block -> text conversion -----------------------------------------

    def _render_children_text(self, block_id: str) -> str:
        """Render a page/block's children into structured plain text.

        Each top-level block (with its nested children) becomes one paragraph
        unit separated by a blank line, so the chunker's paragraph-aware split
        has natural boundaries to work with.
        """
        from notion_client.helpers import iterate_paginated_api

        parts: list[str] = []
        for block in iterate_paginated_api(
            self._client.blocks.children.list, block_id=block_id
        ):
            lines = self._render_block(block, depth=0)
            if lines:
                parts.append("\n".join(lines))
        return "\n\n".join(parts)

    def _render_block(self, block: dict, depth: int) -> list[str]:
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
            # A separate page — its own document; don't inline its content here.
            pass
        else:
            # paragraph, callout, toggle, and anything else with rich_text.
            if text:
                lines.append(indent + text)

        # Recurse into nested content (table rows, sub-bullets, toggle bodies).
        if block.get("has_children") and btype != "child_page":
            child_depth = depth + 1 if btype in _INDENTING else depth
            lines.extend(self._render_children_lines(block["id"], child_depth))

        return lines

    def _render_children_lines(self, block_id: str, depth: int) -> list[str]:
        from notion_client.helpers import iterate_paginated_api

        lines: list[str] = []
        for block in iterate_paginated_api(
            self._client.blocks.children.list, block_id=block_id
        ):
            lines.extend(self._render_block(block, depth))
        return lines
