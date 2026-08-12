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

from ..config.settings import IngestSanitizeSettings, NotionSettings
from ..core.exceptions import ConfigurationError, SourceError
from .base import SourceAdapter, SourceDocument, SourceRef

# Block types whose children should be indented one level (nested lists/toggles).
_INDENTING = {"bulleted_list_item", "numbered_list_item", "to_do", "toggle"}

# A page's block tree is rendered recursively with NO depth limit, and every
# block with children fires its own paginated API call — a deeply/widely
# nested page (nested toggles, a long hierarchical checklist, etc.) can build
# an unbounded string entirely inside fetch_document(), before
# sanitize_ingest_text's max_document_chars check ever runs (that check is
# post-fetch, so it only rejects an already-built oversized string — too late
# to stop the memory spike that built it). This is what OOM-crashed a 512MB
# Render instance: the crash happened before a single document was stored,
# with the job's phase stuck at "preparing" and never reaching "embedding" —
# i.e. inside this fetch, not in the embedding call. Bounding the walk itself,
# with a truncation marker on overflow, is the same reasoning already applied
# to GitHub commit diffs in app/githublive/rest.py.
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
    """Drop folder/index pages whose children are already in the same search result.

    Notion ``search`` returns both a parent (e.g. "Syvora Policies") and each
    child policy page. We treat ``child_page`` blocks as separate documents and
    do not inline them into the parent, so the parent usually has *no ingestible
    text* — yet change detection would forever report it as "1 new". If a listed
    page is the ``parent.page_id`` of another listed page, skip the parent; the
    children carry the policy content.
    """
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

    # -- block -> text conversion -----------------------------------------

    def _render_children_text(self, block_id: str) -> str:
        """Render a page/block's children into structured plain text.

        Each top-level block (with its nested children) becomes one paragraph
        unit separated by a blank line, so the chunker's paragraph-aware split
        has natural boundaries to work with.

        ``budget`` is a one-element mutable list (a shared counter threaded
        through the recursion) rather than a return value, because both
        ``_render_block`` and ``_render_children_lines`` recurse into each
        other and each needs to both read and decrement the SAME remaining
        budget — a plain int argument would only ever see the caller's copy.
        Once it hits zero, no further pagination calls are made and no more
        text is appended, regardless of how much more the page actually has.
        """
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
            # A separate page — its own document; don't inline its content here.
            pass
        else:
            # paragraph, callout, toggle, and anything else with rich_text.
            if text:
                lines.append(indent + text)

        # Charge this block's own text against the budget before deciding
        # whether to recurse — a block with a huge amount of its own text but
        # no children must still stop things, not just the recursion depth.
        budget[0] -= sum(len(line) for line in lines)

        # Recurse into nested content (table rows, sub-bullets, toggle bodies)
        # only while budget remains — this is what actually stops an
        # unbounded fan-out of paginated API calls on a deeply/widely nested
        # page, not just capping the final string length.
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
