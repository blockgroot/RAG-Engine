"""Contract for finding personal identifiers in text bound for an outside service."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Found:
    """One identifier in a text.

    ``key`` is the value normalised (digits only, upper case, E.164 for phones),
    so ``98765 43210`` in a document and ``+91 9876543210`` in the question get
    ONE placeholder and the model can still see they are the same number.
    """

    start: int
    end: int
    kind: str
    key: str


class IdentifierDetector(ABC):
    """Finds identifiers whose format can be checked: patterns and check digits,
    never a guess about what a word means."""

    @abstractmethod
    def find(self, text: str) -> list[Found]:
        """Non-overlapping matches, in text order."""
