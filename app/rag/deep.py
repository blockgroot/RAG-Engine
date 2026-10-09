"""Chat's "Deep analysis" mode for the current request.

A ContextVar for the reason `use_model` is one (app/llm/routed.py): the API
edge sets it, and retrieval (a bigger pool, a document per strong match) and
the pipeline (deep reads, a bigger upload read) both read it, without a flag
threaded through every call between them.
"""

from __future__ import annotations

import contextvars

_DEEP_READ: contextvars.ContextVar[bool] = contextvars.ContextVar("rag_deep_read", default=False)


def use_deep_read(on: bool) -> contextvars.Token:
    """Read deeply and widely for the rest of this request."""
    return _DEEP_READ.set(on)


def reset_deep_read(token: contextvars.Token) -> None:
    _DEEP_READ.reset(token)


def deep_read_on() -> bool:
    return _DEEP_READ.get()
