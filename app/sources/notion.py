"""Notion implementation of the ``SourceAdapter`` interface."""

from __future__ import annotations

import logging
from datetime import datetime

from ..config.settings import IngestSanitizeSettings, NotionSettings
from ..core.exceptions import ConfigurationError, SourceError
from .base import SourceAdapter, SourceDocument, SourceRef
from .meta import build_meta, container, link, person, resolve_link

logger = logging.getLogger(__name__)

_INDENTING = {"bulleted_list_item", "numbered_list_item", "to_do", "toggle"}

_TRUNCATION_MARKER = "\n\n[... content truncated: page exceeds ingest size limit ...]"


def _rich_text_to_text(rich_text: list[dict]) -> str:
    """Flatten a Notion ``rich_text`` array to its plain-text content."""
    return "".join(rt.get("plain_text", "") for rt in (rich_text or []))


def _collect_rich_text(rich_text: list[dict], sink: dict | None) -> None:
    """Record the ids a rich_text array carries before it is flattened.

    `_rich_text_to_text` keeps only ``plain_text``, which turns an @-mention
    into a bare name and a link into its label. The user id, the page id and
    the href are only here, so they are read in the same pass (Second Brain
    1.1) — no extra call, the blocks are already fetched.
    """
    if sink is None:
        return
    for rt in rich_text or []:
        href = rt.get("href")
        if href:
            resolved = resolve_link(href)
            if resolved:
                sink["links"].append(resolved)
        if rt.get("type") != "mention":
            continue
        mention = rt.get("mention") or {}
        kind = mention.get("type")
        if kind == "user":
            user = mention.get("user") or {}
            sink["people"].append(
                person(
                    "notion",
                    role="mentioned",
                    external_id=user.get("id"),
                    email=(user.get("person") or {}).get("email"),
                    name=user.get("name") or (rt.get("plain_text") or "").lstrip("@") or None,
                )
            )
        elif kind in ("page", "database"):
            target = (mention.get(kind) or {}).get("id")
            if target:
                sink["links"].append(link("notion", target))
        elif kind == "link_preview":
            resolved = resolve_link((mention.get("link_preview") or {}).get("url") or "")
            if resolved:
                sink["links"].append(resolved)


def _parent_container(page: dict) -> dict | None:
    parent = page.get("parent") or {}
    kind = parent.get("type")
    if kind in ("page_id", "database_id"):
        return container("notion", kind.removesuffix("_id"), parent.get(kind))
    return None


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
        # Notion's page objects carry `last_edited_by` as an ID only, so a name
        # costs one `GET /users/{id}` per DISTINCT person -- cached for the life
        # of the adapter, the same trick `slack.py::_display_name` uses. A
        # workspace of 500 pages edited by 8 people is 8 calls, not 500.
        self._editor_names: dict[str, str] = {}
        #: Filled from the same `users.retrieve` reply; empty when the
        #: integration lacks the "read user emails" capability.
        self._editor_emails: dict[str, str] = {}

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
                    last_editor=self._editor_name(page),
                )
                for page in _exclude_index_parents(results)
            ]
        except Exception as exc:  # notion APIResponseError / httpx errors
            raise SourceError(f"Notion list_documents failed: {exc}", cause=exc) from exc

    def fetch_document(self, external_id: str) -> SourceDocument:
        try:
            page = self._client.pages.retrieve(page_id=external_id)
            sink: dict = {"people": [], "links": []}
            content = self._render_children_text(external_id, sink=sink)
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
            last_editor=self._editor_name(page),
            meta=self._page_meta(page, sink),
        )

    def _page_meta(self, page: dict, sink: dict) -> dict | None:
        """Editor, creator, parent and in-body mentions — all already in hand.

        The editor was resolved (and cached) by `_editor_name` just above. The
        creator is recorded by id, with a name only when that same person was
        already looked up: resolving a creator separately would be one more
        `users.retrieve` per distinct person, which this field must never cost.
        """
        people = []
        for field, role in (("last_edited_by", "editor"), ("created_by", "creator")):
            user_id = (page.get(field) or {}).get("id")
            if user_id:
                people.append(
                    person(
                        "notion",
                        role=role,
                        external_id=user_id,
                        email=self._editor_emails.get(user_id),
                        name=self._editor_names.get(user_id) or None,
                    )
                )
        return build_meta(
            people=people + sink["people"],
            links=sink["links"],
            containers=[_parent_container(page)],
        )

    def _editor_name(self, page: dict) -> str | None:
        """Who last edited the page, resolved from its `last_edited_by` id.

        Best-effort and cached: a failed lookup returns None rather than
        failing the whole sync, because a missing editor name costs one row in
        one chart while a raised error costs the entire ingest. A bot edit
        resolves to the integration's own name, which is the truth.
        """
        editor = page.get("last_edited_by") or {}
        user_id = editor.get("id")
        if not user_id:
            return None
        if user_id in self._editor_names:
            return self._editor_names[user_id] or None

        try:
            user = self._client.users.retrieve(user_id=user_id)
            name = (user.get("name") or "").strip()
            email = ((user.get("person") or {}).get("email") or "").strip().lower()
            if email:
                self._editor_emails[user_id] = email
        except Exception:  # noqa: BLE001 - see docstring
            logger.debug("Notion users.retrieve(%s) failed", user_id, exc_info=True)
            name = ""
        # Cached even when empty, so a permanently unresolvable id is looked up
        # once per sync rather than once per page.
        self._editor_names[user_id] = name
        return name or None

    def get_last_modified(self, external_id: str) -> datetime | None:
        try:
            page = self._client.pages.retrieve(page_id=external_id)
        except Exception as exc:
            raise SourceError(
                f"Notion get_last_modified({external_id}) failed: {exc}", cause=exc
            ) from exc
        return _parse_dt(page.get("last_edited_time"))

    def _render_children_text(self, block_id: str, sink: dict | None = None) -> str:
        """Render a page or block's children into structured plain text."""
        from notion_client.helpers import iterate_paginated_api

        budget = [IngestSanitizeSettings.from_env().max_document_chars]
        parts: list[str] = []
        for block in iterate_paginated_api(
            self._client.blocks.children.list, block_id=block_id
        ):
            if budget[0] <= 0:
                break
            lines = self._render_block(block, depth=0, budget=budget, sink=sink)
            if lines:
                parts.append("\n".join(lines))
        text = "\n\n".join(parts)
        if budget[0] <= 0:
            text += _TRUNCATION_MARKER
        return text

    def _render_block(
        self, block: dict, depth: int, budget: list[int], sink: dict | None = None
    ) -> list[str]:
        if budget[0] <= 0:
            return []
        btype = block.get("type", "")
        data = block.get(btype, {}) or {}
        indent = "  " * depth
        text = _rich_text_to_text(data.get("rich_text", []))
        _collect_rich_text(data.get("rich_text", []), sink)
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
            for cell in cells:
                _collect_rich_text(cell, sink)
            lines.append(f"{indent}" + " | ".join(_rich_text_to_text(c) for c in cells))
        elif btype == "child_page":
            pass
        else:
            if text:
                lines.append(indent + text)

        budget[0] -= sum(len(line) for line in lines)

        if block.get("has_children") and btype != "child_page" and budget[0] > 0:
            child_depth = depth + 1 if btype in _INDENTING else depth
            lines.extend(self._render_children_lines(block["id"], child_depth, budget, sink))

        return lines

    def _render_children_lines(
        self, block_id: str, depth: int, budget: list[int], sink: dict | None = None
    ) -> list[str]:
        from notion_client.helpers import iterate_paginated_api

        lines: list[str] = []
        for block in iterate_paginated_api(
            self._client.blocks.children.list, block_id=block_id
        ):
            if budget[0] <= 0:
                break
            lines.extend(self._render_block(block, depth, budget, sink))
        return lines
