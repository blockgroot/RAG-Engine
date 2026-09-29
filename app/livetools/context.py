"""The request-scoped switch that makes a question a deep research question.

A ContextVar for the reason ``GraphPlan`` is one (``app/graph/plan.py``): the
chat edge knows the asker and the mode, the pipeline several calls down knows
the hits, and threading four parameters through every agent would change
every agent's signature for a mode most requests never use. No LiveRequest set
means NO live read anywhere -- that absence is what keeps normal Q&A unchanged
(plan D0).
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass(frozen=True)
class LiveRequest:
    """Who is asking, where, and on which conversation -- for scope and audit."""

    org_id: str
    workspace_id: str | None
    user_id: str | None
    conversation_id: str | None = None


_CURRENT: contextvars.ContextVar[LiveRequest | None] = contextvars.ContextVar(
    "live_request", default=None
)


def use_live_request(request: LiveRequest | None) -> contextvars.Token:
    return _CURRENT.set(request)


def reset_live_request(token: contextvars.Token) -> None:
    _CURRENT.reset(token)


def current_live_request() -> LiveRequest | None:
    return _CURRENT.get()
