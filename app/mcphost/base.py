"""Handbook's own MCP servers: a vendor's REGULAR API behind the MCP protocol.

Slack's hosted MCP server accepts only internal or Marketplace apps, and
Google's (Drive, Sheets) only projects in its Workspace Developer Preview. Their
regular APIs have neither gate, so Handbook serves those tools itself
(docs/plans/2026-10-22-mcp-only-actions.md), the way Onyx calls them directly.
Agent mode still reaches them only through MCP: the presets point at
``/mcp/<name>``, so cards, approvals, grounding and space rules are unchanged,
and the package can move to its own service by changing that address.

A server holds no credentials. Each call carries the PERSON's own token as the
bearer (their Slack or Google sign-in under Account → Agent); the server only
forwards it to the vendor, so it can do nothing that person cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..core.exceptions import ProviderError


class VendorError(ProviderError):
    """The vendor refused the call; said to the person as the tool's answer."""


class VendorAuthError(VendorError):
    """The person's sign-in no longer works: answered as HTTP 401, so Handbook
    asks them to sign in again."""


@dataclass(frozen=True)
class ToolResult:
    text: str
    id: str | None = None
    url: str | None = None
    #: What the record is called, so the next card shows its name, not its id.
    name: str | None = None


@dataclass(frozen=True)
class HostedTool:
    name: str
    title: str
    description: str
    schema: dict[str, Any]
    read_only: bool
    run: Callable[[str, dict[str, Any]], ToolResult]

    def listing(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "description": self.description,
                "inputSchema": {"type": "object", **self.schema},
                "annotations": {"title": self.title, "readOnlyHint": self.read_only}}


#: How much of a vendor's text one answer carries; the rest is cut and said.
MAX_TEXT = 20_000


def bounded(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[… cut short]"
