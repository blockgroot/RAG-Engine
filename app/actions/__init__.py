"""Act mode: writes into connected tools, only after the person approves them.

Kept apart from Ask and Chart: nothing on those paths imports this package.
Writes go only through MCP servers an admin added (docs/plans/2026-10-22-mcp-only-actions.md).
"""

from __future__ import annotations

from .base import (
    Action,
    ActionContext,
    ActionResult,
    ActionTarget,
    Audience,
    Proposal,
    Source,
)
from .exposure import Exposure, assess
from .factory import build_actions, get_action

__all__ = [
    "Action",
    "ActionContext",
    "ActionResult",
    "ActionTarget",
    "Audience",
    "Exposure",
    "Proposal",
    "Source",
    "assess",
    "build_actions",
    "get_action",
]
