"""Who is asking, for live reads, as a ContextVar (the chat edge knows the asker,
the pipeline knows the hits). No LiveRequest set means NO live read: only the
web chat sets one. LIVE_TOOLS_ENABLED decides whether a read then happens.
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
    #: ``AskIntent.needs_live``, set after routing. ``None`` = no verdict: the read goes ahead.
    needs_live: bool | None = None


_CURRENT: contextvars.ContextVar[LiveRequest | None] = contextvars.ContextVar(
    "live_request", default=None
)


def use_live_request(request: LiveRequest | None) -> contextvars.Token:
    return _CURRENT.set(request)


def reset_live_request(token: contextvars.Token) -> None:
    _CURRENT.reset(token)


def current_live_request() -> LiveRequest | None:
    return _CURRENT.get()
