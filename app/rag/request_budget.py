"""Per-request time budget passed through the RAG pipeline (Phase 19)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..config.settings import RequestBudgetSettings


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
        """True if at least ``seconds`` of budget remain."""
        return self.remaining_seconds() >= seconds
