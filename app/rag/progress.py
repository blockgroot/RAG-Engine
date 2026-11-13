"""Live progress lines for the chat stream: what the answer is doing now.

Lines come from steps that really ran; titles only from hits that passed
the org, workspace and viewer filters, never model text. A ContextVar
sink set at the API edge; with no sink (Slack, eval) a report is free.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Callable

from .context_assemble import _PROVIDER_LABEL

logger = logging.getLogger(__name__)

_SINK: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar(
    "rag_progress_sink", default=None
)

#: Titles named per app; more would be a list nobody reads while waiting.
_MAX_TITLES = 3


def use_progress(sink: Callable[[str], None] | None) -> contextvars.Token:
    return _SINK.set(sink)


def reset_progress(token: contextvars.Token) -> None:
    _SINK.reset(token)


def report(text: str) -> None:
    """Send one line, if anyone is listening. Never raises: progress is a nicety."""
    sink = _SINK.get()
    if sink is None:
        return
    try:
        sink(text)
    except Exception:  # noqa: BLE001
        logger.debug("progress sink failed", exc_info=True)


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def searching(agent_key: str | None, connected: set[str] | None = None) -> None:
    """Which app(s) the router sent the question to."""
    keys = sorted(connected) if connected else [agent_key or ""]
    labels = [_PROVIDER_LABEL[k] for k in keys if k in _PROVIDER_LABEL]
    if agent_key == "insights" and not connected:
        report("Counting from your connected apps")
    elif labels:
        report(f"Searching {_join(labels)}")
    else:
        report("Searching your documents")


def found(hits: list, *, deep_docs: int = 0) -> None:
    """What retrieval put in front of the model, grouped by app."""
    by_app: dict[str, list[str]] = {}
    docs: set[str] = set()
    for h in hits:
        if h is None:
            continue
        docs.add(h.document_id)
        title = (getattr(h, "document_title", None) or "").strip()
        if not title:
            continue
        provider = getattr(h, "source_provider", None) or ""
        editor = (getattr(h, "last_editor", None) or "").strip()
        # A Slack thread is a conversation; who wrote it is what identifies it.
        name = f"“{title}” from {editor}" if provider == "slack" and editor else f"“{title}”"
        names = by_app.setdefault(_PROVIDER_LABEL.get(provider, "your documents"), [])
        if name not in names:
            names.append(name)
    for app, names in by_app.items():
        extra = len(names) - _MAX_TITLES
        shown = names[:_MAX_TITLES] + ([f"{extra} more"] if extra > 0 else [])
        report(f"Found in {app}: {_join(shown)}")
    if deep_docs and docs:
        n = min(len(docs), deep_docs)
        report("Reading that document in full" if n == 1 else f"Reading the top {n} documents in full")
