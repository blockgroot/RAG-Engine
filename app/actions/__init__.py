"""Act mode: writes into connected tools, only after the person approves them.

Nothing on the Ask or Chart paths imports this package. Writes go only through
MCP servers an admin added.
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
