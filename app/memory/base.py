"""The conversation-memory contract (Phase 5).

Groups a sequence of question/answer turns under a ``conversation_id`` so a
follow-up ("what about part-timers?") can be resolved against prior context
before retrieval. Kept behind an interface + factory like every other capability,
so the backing store (Postgres now) can be swapped without touching callers.

Storage split, mirroring the summarization design: recent turns are kept verbatim
(``get_context`` returns them), older turns get compressed into a running
``summary`` and pruned (``set_summary_and_prune``). Everything is org-scoped.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Turn:
    """One question + its answer within a conversation."""

    turn_index: int
    question: str
    answer: str


@dataclass(frozen=True)
class ConversationContext:
    """What the query-rewriter sees: the running summary + recent verbatim turns."""

    summary: str | None = None
    recent_turns: list[Turn] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.summary and not self.recent_turns


class ConversationStore(ABC):
    """Abstract, org-scoped store for conversation history."""

    @abstractmethod
    def create_conversation(self, org_id: str) -> str:
        """Create a conversation for a tenant and return its ``conversation_id``."""
        raise NotImplementedError

    @abstractmethod
    def append_turn(self, conversation_id: str, question: str, answer: str) -> int:
        """Append a turn; return its 0-based ``turn_index``."""
        raise NotImplementedError

    @abstractmethod
    def get_turns(self, conversation_id: str) -> list[Turn]:
        """Return all stored (non-pruned) turns, oldest first."""
        raise NotImplementedError

    @abstractmethod
    def get_summary(self, conversation_id: str) -> str | None:
        """Return the running summary of pruned older turns, if any."""
        raise NotImplementedError

    @abstractmethod
    def get_context(self, conversation_id: str, recent_turns: int) -> ConversationContext:
        """Return the summary plus the most recent ``recent_turns`` turns."""
        raise NotImplementedError

    @abstractmethod
    def set_summary_and_prune(
        self, conversation_id: str, summary: str, keep_recent: int
    ) -> None:
        """Store ``summary`` and delete all but the most recent ``keep_recent`` turns."""
        raise NotImplementedError
