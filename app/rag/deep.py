"""Chat's "Deep analysis" mode for the current request.

A ContextVar for the reason `use_model` is one (app/llm/routed.py): the API
edge sets it, and retrieval (a bigger pool, a document per strong match) and
the pipeline (deep reads, a bigger upload read) both read it, without a flag
threaded through every call between them.

"Extra effort" is Deep's opt-in for company-wide and list questions: breadth
(up to ``deep_wide_max_hits`` documents) as well as depth, a longer answer and
the assemble rule. It costs more per answer, so plain Deep does not pay it.
"""

from __future__ import annotations

import contextvars

#: "" off, "deep" Deep analysis, "extra" Deep analysis with extra effort.
_DEEP_READ: contextvars.ContextVar[str] = contextvars.ContextVar("rag_deep_read", default="")


def use_deep_read(on: bool, *, extra: bool = False) -> contextvars.Token:
    """Read deeply for the rest of this request; ``extra`` also widely."""
    return _DEEP_READ.set("extra" if on and extra else "deep" if on else "")


def reset_deep_read(token: contextvars.Token) -> None:
    _DEEP_READ.reset(token)


def deep_read_on() -> bool:
    return bool(_DEEP_READ.get())


def extra_effort_on() -> bool:
    return _DEEP_READ.get() == "extra"
