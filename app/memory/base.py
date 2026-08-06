"""The conversation-memory contract (Phase 5).

Groups a sequence of question/answer turns under a ``conversation_id`` so a
follow-up ("what about part-timers?") can be resolved against prior context
before retrieval. Kept behind an interface + factory like every other capability,
so the backing store (Postgres now) can be swapped without touching callers.

Storage split, mirroring the summarization design: recent turns are kept verbatim
(``get_context`` returns them), older turns get compressed into a running
``summary`` and pruned (``set_summary_and_prune``). Everything is org-scoped.

Phase 8 adds ``set_last_retrieval`` / ``get_last_retrieval`` so the pipeline can
remember one turn's retrieved chunks and cheaply decide, on the next turn, whether
to reuse them instead of retrieving again.
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


@dataclass(frozen=True)
class RetrievedChunkRecord:
    """A retrieved chunk remembered from a turn, for the Phase 8 reuse check.

    Deliberately *not* the vectorstore's ``RetrievedChunk`` — the memory layer
    stays ignorant of the retrieval layer. It holds only what the next turn needs
    to (a) re-embed the chunk and re-score it against the new question and (b)
    reconstruct a citation: the text plus its stable locator. Embeddings are NOT
    stored — they are cheaply recomputed from ``content`` when needed (see
    ``app/rag/pipeline.py`` for that tradeoff), so no vector columns are added.
    """

    content: str
    document_id: str
    chunk_index: int
    org_id: str
    document_title: str | None = None


class ConversationStore(ABC):
    """Abstract, org-scoped store for conversation history."""

    @abstractmethod
    def create_conversation(self, org_id: str, workspace_id: str | None = None) -> str:
        """Create a conversation for a tenant and return its ``conversation_id``.

        ``workspace_id`` (Workspace-within-a-Workspace): ``None`` (default)
        creates an org-wide conversation, unchanged from every prior caller.
        A non-``None`` value stamps which sub-workspace this conversation
        belongs to, so the API layer can later verify a client-supplied
        ``conversation_id`` actually belongs to the caller's workspace.
        """
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

    # -- Phase 8: last-turn retrieval, for the cheap retrieval-reuse check ----

    @abstractmethod
    def set_last_retrieval(
        self, conversation_id: str, org_id: str, chunks: list[RetrievedChunkRecord]
    ) -> None:
        """Remember the chunks retrieved on the latest turn (replacing any prior).

        Only the most recent turn's chunks are kept — the reuse check only ever
        looks one turn back. An empty ``chunks`` list clears the memory (so a turn
        that retrieved nothing, or a web/fallback answer, can't be reused).
        """
        raise NotImplementedError

    @abstractmethod
    def get_last_retrieval(self, conversation_id: str) -> list[RetrievedChunkRecord]:
        """Return the chunks remembered from the latest turn (empty if none)."""
        raise NotImplementedError
