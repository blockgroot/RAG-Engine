"""Which servers Handbook hosts, by the name in their address (``/mcp/<name>``)."""

from __future__ import annotations

from .base import HostedTool


def hosted_tools(name: str) -> list[HostedTool] | None:
    if name == "slack":
        from .slack import TOOLS
    elif name == "google":
        from .google import TOOLS
    else:
        return None
    return TOOLS
