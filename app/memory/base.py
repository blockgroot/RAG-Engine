"""The conversation-memory contract.

Turns grouped under a ``conversation_id`` so a follow-up resolves against
prior context. Recent turns verbatim; older ones folded into ``summary``
(``folded_through`` marks how far), but kept as the transcript. Org-scoped.
``set_last_retrieval``/``get_last_retrieval`` let the next turn reuse chunks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Turn:
    """One question + its answer. ``cited`` and ``chart`` are what the web chat
    drew; the rewriter reads neither.
    """

    turn_index: int
    question: str
    answer: str
    cited: list = field(default_factory=list)
    chart: dict | None = None


@dataclass(frozen=True)
class ConversationContext:
    """What the query-rewriter sees: the running summary + recent verbatim turns."""

    summary: str | None = None
    recent_turns: list[Turn] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.summary and not self.recent_turns


@dataclass(frozen=True)
class RetrievedChunkRecord:
    """A retrieved chunk remembered from a turn, for the reuse check.

    Not ``RetrievedChunk``: memory stays ignorant of retrieval. Holds text and
    locator only; embeddings are recomputed when needed.
    """

    content: str
    document_id: str
    chunk_index: int
    org_id: str
    document_title: str | None = None


class ConversationStore(ABC):
    """Abstract, org-scoped store for conversation history."""

    @abstractmethod
    def create_conversation(
        self,
        org_id: str,
        workspace_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """Create a conversation for a tenant and return its ``conversation_id``.

        ``workspace_id`` stamps the space (None = org-wide) so the API can
        check a resumed id. ``user_id`` is the owner, required to match on
        resume; None (tests, scripts) leaves it ownerless.
        """
        raise NotImplementedError

    @abstractmethod
    def append_turn(
        self, conversation_id: str, question: str, answer: str, cited: list | None = None,
        chart: dict | None = None,
    ) -> int:
        """Append a turn; return its 0-based ``turn_index``. ``cited`` and
        ``chart`` (``{"panel", "period"}``) are optional.
        """
        raise NotImplementedError

    @abstractmethod
    def get_turns(self, conversation_id: str) -> list[Turn]:
        """Return every stored turn, oldest first."""
        raise NotImplementedError

    @abstractmethod
    def get_summary(self, conversation_id: str) -> str | None:
        """Return the running summary of older turns, if any."""
        raise NotImplementedError

    @abstractmethod
    def get_context(self, conversation_id: str, recent_turns: int) -> ConversationContext:
        """Return the summary plus the most recent ``recent_turns`` turns."""
        raise NotImplementedError

    @abstractmethod
    def get_folded_through(self, conversation_id: str) -> int | None:
        """Highest ``turn_index`` already folded into the summary, or None."""
        raise NotImplementedError

    @abstractmethod
    def set_summary_folded_through(
        self, conversation_id: str, summary: str, folded_through: int
    ) -> None:
        """Store ``summary`` and record how far it has folded. Turns are kept:
        they are the person's transcript; the marker stops re-folding.
        """
        raise NotImplementedError

    # -- Phase 8: last-turn retrieval, for the cheap retrieval-reuse check ----

    @abstractmethod
    def set_last_retrieval(
        self, conversation_id: str, org_id: str, chunks: list[RetrievedChunkRecord]
    ) -> None:
        """Remember the latest turn's chunks (the reuse check looks one turn
        back). An empty list clears it, so a fallback answer is never reused.
        """
        raise NotImplementedError

    @abstractmethod
    def get_last_retrieval(self, conversation_id: str) -> list[RetrievedChunkRecord]:
        """Return the chunks remembered from the latest turn (empty if none)."""
        raise NotImplementedError
