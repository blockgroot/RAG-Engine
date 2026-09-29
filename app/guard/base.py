"""Contract for scoring untrusted text for prompt injection."""

from __future__ import annotations

from abc import ABC, abstractmethod


class InjectionGuard(ABC):
    """Scores text for "this reads like instructions to an AI".

    ``score`` returns one value per input, in order: a probability in [0, 1],
    or ``None`` when that text could not be scored. ``None`` is never 0.0 — a
    rate-limited call must read as "unknown" so a backfill picks it up, not as
    "clean" forever.
    """

    model: str

    @abstractmethod
    def score(self, texts: list[str]) -> list[float | None]: ...
