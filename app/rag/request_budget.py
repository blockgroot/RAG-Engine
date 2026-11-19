"""Per-request time budget passed through the RAG pipeline."""

from __future__ import annotations

import contextvars
import threading
import time
from dataclasses import dataclass

from ..config.settings import RequestBudgetSettings


#: Set when the asker left (the stream's client disconnected): every budget in
#: this request then has nothing left, so optional stages (audit, retries,
#: escalation) are skipped instead of paid for and thrown away.
_ABANDONED: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "request_abandoned", default=None)


def watch_abandon() -> threading.Event:
    """A flag for this request's budgets; ``set()`` it when the asker leaves.

    A context variable, so the worker threads a stream runs in see it (each
    threadpool call copies the context, and the Event itself is shared).
    """
    flag = threading.Event()
    _ABANDONED.set(flag)
    return flag


@dataclass
class RequestBudget:
    """Monotonic deadline for one ``answer()`` call."""

    deadline: float

    @classmethod
    def from_settings(cls, settings: RequestBudgetSettings | None = None) -> RequestBudget:
        settings = settings or RequestBudgetSettings.from_env()
        return cls(deadline=time.monotonic() + settings.deadline_seconds)

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def can_spend(self, seconds: float) -> bool:
        """True if at least ``seconds`` of budget remain (none once the asker left)."""
        flag = _ABANDONED.get()
        if flag is not None and flag.is_set():
            return False
        return self.remaining_seconds() >= seconds
